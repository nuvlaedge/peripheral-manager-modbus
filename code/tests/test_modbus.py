import mock
import unittest

from modbus.modbus import main


class TestModbus(unittest.TestCase):

    @unittest.skip("dummy test")
    def test_main(self):
        """Dummy test."""
        main = mock.Mock()
        main.return_value = None
        assert None is main()
