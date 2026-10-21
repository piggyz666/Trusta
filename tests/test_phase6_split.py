import tempfile
import time
import unittest
from backend.banking.split import allocate
from backend.domain import digest, Rejected
from tests.phase6_helpers import service,seed_participants

class SplitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=service(self.temp.name);seed_participants(self.s)

    def bill(self,total=10000,**kwargs):
        b=self.s.splits.create('demo','acct-demo',total,['alice','bob','carol'],**kwargs)
        return self.s.splits.publish(b['id'],'demo')

    def pay(self,b,user):
        t=self.s.splits.prepare_payment(b['id'],user,'acct-'+user)
        return self.s.splits.pay(b['id'],user,digest(t['proposed_actions'][0]),True)

    def test_one_cent_three_people_no_zero_transfer(self):
        self.assertEqual(allocate(1,['alice','bob','carol']),{'alice':1,'bob':0,'carol':0})
        b=self.bill(1)
        with self.assertRaises(Rejected): self.s.splits.prepare_payment(b['id'],'bob','acct-bob')
        self.pay(b,'alice');self.assertEqual(self.s.splits.get(b['id'],'demo')['status'],'COMPLETED')
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_hundred_yuan_three_people_exact_sum(self):
        self.assertEqual(list(allocate(10000,['alice','bob','carol']).values()),[3334,3333,3333])

    def test_percentage_decimal_largest_remainder(self):
        self.assertEqual(list(allocate(1,['a','b','c'],'PERCENTAGE',['33.3333','33.3333','33.3334']).values()),[0,0,1])
        self.assertEqual(sum(allocate(10000,['a','b','c'],'PERCENTAGE',['33.3333','33.3333','33.3334']).values()),10000)

    def test_exact_and_percentage_invalid_inputs(self):
        self.assertEqual(allocate(100,['a','b'],'EXACT_AMOUNT',[90,10]),{'a':90,'b':10})
        for args in [(100,['a','b'],'EXACT_AMOUNT',[99,0]),(100,['a','b'],'PERCENTAGE',['NaN','100']),(100,['a','b'],'PERCENTAGE',[50.0,50.0]),(True,['a','b'],'EQUAL',None),(100,['a','a'],'EQUAL',None)]:
            with self.subTest(args=args),self.assertRaises(Rejected): allocate(*args)

    def test_organizer_creation_cannot_debit_participants(self):
        self.bill();self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-alice'")['balance'],100000)

    def test_independent_payment_receipts_real_collection(self):
        b=self.bill();auths=[];hashes=[];receipts=[]
        for u in ('alice','bob','carol'):
            t=self.pay(b,u);self.assertEqual(t['status'],'COMPLETED')
            auths.append(t['authorization']['authorization_id']);hashes.append(t['receipt']['approved_action_hash']);receipts.append(t['receipt']['receipt_id'])
        self.assertEqual(len(set(auths)),3);self.assertEqual(len(set(hashes)),3);self.assertEqual(len(set(receipts)),3)
        self.assertEqual(self.s.splits.get(b['id'],'demo')['status'],'COMPLETED')
        self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")['balance'],1010000)
        self.assertTrue(self.s.audit.verify_chain())

    def test_partial_payment_restart(self):
        b=self.bill();self.pay(b,'alice');self.assertEqual(self.s.splits.get(b['id'],'demo')['status'],'PARTIALLY_PAID')
        self.s=service(self.temp.name);self.pay(b,'bob');self.pay(b,'carol')
        self.assertEqual(self.s.splits.get(b['id'],'demo')['status'],'COMPLETED')

    def test_organizer_and_cross_participant_cannot_authorize(self):
        b=self.bill();a=self.s.splits.prepare_payment(b['id'],'alice','acct-alice');p=self.s.splits.prepare_payment(b['id'],'bob','acct-bob')
        for who,hash_ in [('demo',digest(a['proposed_actions'][0])),('bob',digest(a['proposed_actions'][0]))]:
            with self.assertRaises(Rejected): self.s.splits.pay(b['id'],who,hash_,True)
        with self.assertRaises(Rejected): self.s.auth.validate(a['authorization']['authorization_id'],p['proposed_actions'][0])
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),0)

    def test_duplicate_payment_never_debits_again(self):
        b=self.bill();t=self.pay(b,'alice')
        with self.assertRaises(Rejected): self.s.splits.pay(b['id'],'alice',digest(t['proposed_actions'][0]),True)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_declines_do_not_reduce_required_amount(self):
        b=self.bill();self.pay(b,'alice');self.s.splits.decline(b['id'],'bob');self.s.splits.decline(b['id'],'carol')
        result=self.s.splits.get(b['id'],'demo');self.assertEqual(result['status'],'PARTIALLY_PAID');self.assertEqual(result['total_amount'],10000)

    def test_expiry_and_direct_task_authorization_cannot_pay_late(self):
        b=self.bill(expires_at=time.time()+0.8);t=self.s.splits.prepare_payment(b['id'],'alice','acct-alice')
        time.sleep(0.85)
        done=self.s.authorize(t['task_id'],'alice',digest(t['proposed_actions'][0]),True)
        self.assertNotEqual(done['status'],'COMPLETED');self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),0)
        self.assertEqual(self.s.splits.get(b['id'],'demo')['status'],'EXPIRED')

    def test_cancel_keeps_paid_and_cancels_pending_auth(self):
        b=self.bill();self.pay(b,'alice');t=self.s.splits.prepare_payment(b['id'],'bob','acct-bob')
        result=self.s.splits.cancel(b['id'],'demo');self.assertEqual(result['status'],'CANCELLED')
        self.assertEqual(result['participants']['alice']['status'],'PAID')
        with self.assertRaises(Rejected): self.s.authorize(t['task_id'],'bob',digest(t['proposed_actions'][0]),True)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_participant_view_and_source_owner_isolation(self):
        b=self.bill();self.assertEqual(set(self.s.splits.get(b['id'],'alice')['participants']),{'alice'})
        with self.assertRaises(Rejected): self.s.splits.get(b['id'],'other')
        with self.assertRaises(Rejected): self.s.splits.prepare_payment(b['id'],'alice','acct-bob')

    def test_unknown_receiver_and_unknown_participant(self):
        with self.assertRaises(Rejected): self.s.splits.create('demo','acct-alice',100,['bob','carol'])
        with self.assertRaises(Rejected): self.s.splits.create('demo','acct-demo',100,['alice','other'])

    def test_draft_and_publish_authority(self):
        b=self.s.splits.create('demo','acct-demo',100,['alice','bob'])
        self.assertEqual(b['status'],'DRAFT')
        with self.assertRaises(Rejected): self.s.splits.publish(b['id'],'alice')
        with self.assertRaises(Rejected): self.s.splits.prepare_payment(b['id'],'alice','acct-alice')
