"""The bank conformance checker (scripts/bank_check.py) and the adapter template it is meant to be used with."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from scripts import bank_check

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "examples", "bank_adapter", "partner-bank.example.toml")


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = bank_check.main(list(argv))
    return code, out.getvalue()


def broken(raw, db):
    """The template with a typical mistake: amounts sent as decimal strings instead of integer minor units."""
    from examples.bank_adapter.partner_bank import PartnerBank

    class Broken(PartnerBank):
        def list_transactions(self, req, params):
            rows, status = super().list_transactions(req, params)
            return [dict(r, amount=f"{r['amount'] / 100:.2f}") for r in rows], status

    return Broken(raw, db)


class BankCheckTests(unittest.TestCase):
    def test_sandbox_passes(self):
        code, out = run("--sandbox", "--quote")
        self.assertEqual(code, 0, out)
        self.assertIn("定期存款报价", out)

    def test_template_passes_including_a_verified_sandbox_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            report = os.path.join(folder, "r.json")
            code, out = run("--config", TEMPLATE, "--write-test", "transfer", "--payee", "张三", "--amount", "100", "--report", report)
            self.assertEqual(code, 0, out)
            data = json.load(open(report, encoding="utf-8"))
            write = next(i for i in data["items"] if i["section"] == "writes")
            self.assertEqual(write["status"], bank_check.PASS)
            self.assertIn("VERIFIED", write["detail"])

    def test_format_mistakes_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            config = os.path.join(folder, "bad.toml")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write('provider = "module"\nfactory = "tests.test_bank_check:broken"\ncustomer_id = "demo"\n')
            code, out = run("--config", config)
            self.assertEqual(code, 1)
            self.assertIn("amount: 应为 integer", out)

    def test_bad_configuration_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            config = os.path.join(folder, "bad.toml")
            with open(config, "w", encoding="utf-8") as handle:
                handle.write('provider = "module"\nfactory = "no_such_module:create"\n')
            code, out = run("--config", config)
            self.assertEqual(code, 1)
            self.assertIn("INVALID_BANK_PROVIDER_CONFIGURATION", out)

    def test_production_is_never_written(self):
        with tempfile.TemporaryDirectory() as folder:
            config = os.path.join(folder, "prod.toml")
            text = open(TEMPLATE, encoding="utf-8").read().replace('environment = "sandbox"', 'environment = "production"')
            open(config, "w", encoding="utf-8").write(text)
            code, out = run("--config", config, "--write-test", "transfer", "--payee", "张三", "--amount", "100")
            self.assertIn("生产环境不做写入", out)

    def test_schemas_export(self):
        from backend.bank_sdk.schemas import SCHEMAS
        with tempfile.TemporaryDirectory() as folder:
            code, out = run("--export-schemas", folder)
            self.assertEqual(code, 0)
            self.assertEqual(sorted(f[:-5] for f in os.listdir(folder)), sorted(SCHEMAS))
            document = json.load(open(os.path.join(folder, "finance_snapshot.json"), encoding="utf-8"))
            self.assertEqual(document["properties"]["target"]["$ref"], "#/$defs/finance_contract")

    def test_committed_schemas_are_current(self):
        root = os.path.join(os.path.dirname(__file__), "..", "docs", "schemas")
        with tempfile.TemporaryDirectory() as folder:
            run("--export-schemas", folder)
            for name in os.listdir(folder):
                self.assertEqual(open(os.path.join(folder, name), encoding="utf-8").read(),
                                 open(os.path.join(root, name), encoding="utf-8").read(), f"re-run --export-schemas docs/schemas ({name})")

    def test_contract_document_covers_every_schema(self):
        from backend.bank_sdk.schemas import SCHEMAS
        text = open(os.path.join(os.path.dirname(__file__), "..", "docs", "bank-data-contract.md"), encoding="utf-8").read()
        for name in SCHEMAS:
            self.assertIn(f"`{name}`", text, name)


if __name__ == "__main__":
    unittest.main()
