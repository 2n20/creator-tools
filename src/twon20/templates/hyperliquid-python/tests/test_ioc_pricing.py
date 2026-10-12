from decimal import Decimal
import unittest

from executor import execution_state, plan_child
from risk import Stop
from test_runtime import config, intent, snapshot


class IocPricingTests(unittest.TestCase):
    def setUp(self):
        self.limits = config()['execution']
        self.limits.update(iocAfterSeconds=5, slippageBps=20, childNotional='12')
        self.state = execution_state(1000)

    def test_ioc_buy_can_match_a_moved_ask_within_original_bound(self):
        plan = plan_child(intent(), self.state, snapshot(), self.limits, 6000)
        self.assertEqual(plan['tif'], 'Ioc')
        self.assertEqual(Decimal(plan['price']), Decimal('100.2'))
        self.assertGreaterEqual(Decimal(plan['price']), Decimal('100.15'))
        self.assertLessEqual(Decimal(plan['price']) * Decimal(plan['size']), Decimal('12'))

    def test_reduce_only_ioc_sell_can_match_a_moved_bid(self):
        self.state['position'] = '0.00014'
        current = snapshot('0.00014')
        current.update(mark=Decimal('82616'), bid=Decimal('82592'), ask=Decimal('82593'))
        target = {**intent(fraction='0', quantity='0'), 'arrivalPrice': '82616'}
        plan = plan_child(target, self.state, current, self.limits, 6000)
        self.assertTrue(plan['reduceOnly'])
        self.assertFalse(plan['buy'])
        self.assertEqual(Decimal(plan['size']), Decimal('0.00014'))
        self.assertEqual(Decimal(plan['price']), Decimal('82451'))
        self.assertLessEqual(Decimal(plan['price']), Decimal('82580'))
        self.assertGreaterEqual(Decimal(plan['price']), Decimal('82616') * Decimal('0.998'))

    def test_ioc_rejects_market_beyond_fixed_bound(self):
        for buy in (True, False):
            current = snapshot('0.2' if not buy else '0')
            self.state['position'] = '0' if buy else '0.2'
            target = intent(quantity='1' if buy else '0')
            current.update(ask=Decimal('100.3') if buy else Decimal('99.8'),
                           bid=Decimal('100.2') if buy else Decimal('99.7'))
            with self.assertRaises(Stop) as error:
                plan_child(target, self.state, current, self.limits, 6000)
            self.assertEqual(error.exception.code, 'PRICE_BOUND')

    def test_maker_price_stays_at_passive_quote_until_urgency(self):
        plan = plan_child(intent(), self.state, snapshot(), self.limits, 5999)
        self.assertEqual(plan['tif'], 'Alo')
        self.assertEqual(Decimal(plan['price']), Decimal('99.9'))

    def test_ioc_rounds_toward_the_allowed_side_of_the_bound(self):
        for buy in (True, False):
            current = snapshot('0.2' if not buy else '0')
            current['decimals'] = 5
            self.state['position'] = '0' if buy else '0.2'
            target = {**intent(quantity='1' if buy else '0'), 'arrivalPrice': '100.01'}
            plan = plan_child(target, self.state, current, self.limits, 6000)
            price = Decimal(plan['price'])
            bound = Decimal('100.01') * (Decimal('1.002') if buy else Decimal('.998'))
            self.assertLessEqual(price, bound) if buy else self.assertGreaterEqual(price, bound)
            self.assertEqual(price, price.quantize(Decimal('.1')))
