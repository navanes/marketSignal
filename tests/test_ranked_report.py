import datetime as dt
import unittest
from unittest.mock import patch
import ranked_report as report
import telegram_bot as bot

class ReportTests(unittest.TestCase):
    def test_routes(self):
        for text in ('/top10', '/report crypto', '/top10@mybot', 'top 10 most confident cryptos', 'what is hottest now'):
            self.assertTrue(bot.wants_ranked_report(text), text)
        self.assertFalse(bot.wants_ranked_report('should I buy bitcoin?'))

    def test_freshness_and_conflict(self):
        def scan(symbol, *args):
            return dict(price=100, price_date=(dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=10 if symbol == 'OLD' else 0)).isoformat(), horizons=[
                dict(horizon_days=h, action='Buy / Accumulate', direction='down' if symbol == 'MIX' else 'up', confidence='medium', expected_return_pct=2) for h in report.HORIZONS])
        with patch.object(report, 'UNIVERSE', [('BTC-USD','Bitcoin','crypto'),('OLD','Old','tech'),('MIX','Mixed','tech')]), patch.object(report.app, 'multi_horizon_signals', side_effect=scan):
            data=report.collect()
            self.assertEqual([r['symbol'] for r in data['buckets']['BUY']], ['BTC-USD'])
            self.assertEqual([r['symbol'] for r in data['buckets']['HOLD']], ['MIX'])
            self.assertEqual(data['excluded'][0]['symbol'], 'OLD')
            self.assertEqual(report.collect(True)['scanned'], 1)
            path, _ = report.build_report(data=data)
            self.assertTrue(path.read_bytes().startswith(b'%PDF'))
            path.unlink(); path.with_suffix('.json').unlink()

if __name__ == '__main__':
    unittest.main()
