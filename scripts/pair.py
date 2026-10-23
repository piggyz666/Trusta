"""Pair a phone (or bind Feishu) from this computer, without the web page.

    python -m scripts.pair              手机配对：显示配对地址、二维码（装了 qrcode 包时）和 6 位配对码
    python -m scripts.pair --feishu     飞书绑定：显示要发给机器人的“绑定 123456”
    python -m scripts.pair --devices    列出已配对设备
    python -m scripts.pair --revoke ID  吊销某台设备

Runs against the same local database as the server; codes expire in 5 minutes and work once.
"""
import argparse
import time
from backend.access import AccessControl
from backend.config import Config
from backend.service import TrustaService


def show_qr(text):
    try:
        import qrcode
    except ImportError:
        print("（安装 qrcode 包后可在终端显示二维码：pip install qrcode）")
        return
    code = qrcode.QRCode(border=1)
    code.add_data(text)
    code.print_ascii(invert=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="demo")
    parser.add_argument("--feishu", action="store_true")
    parser.add_argument("--devices", action="store_true")
    parser.add_argument("--revoke", default="")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    service = TrustaService(Config.local(), recover_workflows=False)
    access = AccessControl(service)
    if args.devices:
        for d in access.devices(args.owner):
            state = "已吊销" if d["revoked"] else ("已过期" if d["expires_at"] < time.time() else "有效")
            print(f"{d['id']}  {d['name']:<10} {state}  最近使用 {time.strftime('%m-%d %H:%M', time.localtime(d['last_seen'])) if d['last_seen'] else '—'}")
        return
    if args.revoke:
        print(access.revoke(args.owner, args.revoke))
        return
    result = access.start_pairing(args.owner, "feishu" if args.feishu else "device", args.port)
    if args.feishu:
        print(result["instruction"] + "（5 分钟内有效，只能用一次）")
        return
    print("手机和电脑连同一个 Wi-Fi / 热点，然后扫码或在手机浏览器打开：")
    print("  " + result["pair_url"])
    show_qr(result["pair_url"])
    print(f"或在手机页面手动输入配对码：{result['code']}（5 分钟内有效，只能用一次）")


if __name__ == "__main__":
    main()
