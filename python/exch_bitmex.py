import time
import threading
import json
from functools import partial
from datetime import datetime
from ws_api_socket import WebSocketApiClient
from market_data import L2Depth, Trade
from exchange import ExchangeGateway
from util import print_log

class ExchGwBitmexWs(WebSocketApiClient):
    """
    Exchange gateway BTCC RESTfulApi
    """
    def __init__(self):
        """
        Constructor
        """
        WebSocketApiClient.__init__(self, 'ExchGwBitMEX')
            
    @classmethod
    def parse_l2_depth(cls, instmt, raw):
        """
        Parse raw data to L2 depth
        :param instmt: Instrument
        :param raw: Raw data in JSON
        """
        l2_depth = L2Depth(exch=instmt.get_exchange_name(), instmt=instmt.get_instmt_code())
        field_map = instmt.get_order_book_fields_mapping()
        for key, value in raw.items():
            if key in field_map.keys():
                try:
                    field = field_map[key]
                except:
                    print("Error from order_book_fields_mapping on key %s" % key)
                    raise
                
                if field == 'TIMESTAMP':
                    l2_depth.date_time = value
                elif field == 'BIDS':
                    bids = value
                    sorted(bids, key=lambda x: x[0])
                    bids = bids[0:5]
                    l2_depth.bid = [float(e[0]) if type(e[0]) != float else e[0] for e in bids]
                    l2_depth.bid_volume = [float(e[1]) if type(e[1]) != float else e[1] for e in bids]
                elif field == 'ASKS':
                    asks = value
                    sorted(asks, key=lambda x: x[0], reverse=True)
                    asks = asks[0:5]
                    l2_depth.ask = [float(e[0]) if type(e[0]) != float else e[0] for e in asks]
                    l2_depth.ask_volume = [float(e[1]) if type(e[1]) != float else e[1] for e in asks]
                else:
                    raise Exception('The field <%s> is not found' % field)

        return l2_depth

    @classmethod
    def parse_trade(cls, instmt, raw):
        """
        :param instmt: Instrument
        :param raw: Raw data in JSON
        :return:
        """
        trade = Trade(exch=instmt.get_exchange_name(), instmt=instmt.get_instmt_code())
        field_map = instmt.get_trades_fields_mapping()
        for key, value in raw.items():
            if key in field_map.keys():
                try:
                    field = field_map[key]
                except:
                    print("Error from trades_fields_mapping on key %s" % key)
                    raise
                
                if field == 'TIMESTAMP':
                    trade.date_time = value
                elif field == 'TRADE_SIDE':
                    side = value
                    if type(side) != int:
                        side = side.lower()
                        if side == 'buy':
                            side = 1
                        elif side == 'sell':
                            side = 2
                        else:
                            raise Exception('Unrecognized trade side %s' % side)
                    
                    if side == 1:
                        trade.trade_side = trade.Side.BUY
                    elif side == 2:
                        trade.trade_side = trade.Side.SELL
                    else:
                        print(side)
                        raise Exception('Unexpected trade side value %d' % side)
                        
                elif field == 'TRADE_ID':
                    trade.trade_id = value
                elif field == 'TRADE_PRICE':
                    trade.trade_price = value
                elif field == 'TRADE_VOLUME':
                    trade.trade_volume = value
                else:
                    raise Exception('The field <%s> is not found' % field)        


        return trade

class ExchGwBitmex(ExchangeGateway):
    """
    Exchange gateway BTCC
    """
    def __init__(self, db_client):
        """
        Constructor
        :param db_client: Database client
        """
        ExchangeGateway.__init__(self, ExchGwBitmexWs(), db_client)
        self.db_order_book_id = 0
        self.db_trade_id = 0
        self.last_exch_trade_id = ''
        self.db_order_book_table_name = ''
        self.db_trades_table_name = ''

    @classmethod
    def get_exchange_name(cls):
        """
        Get exchange name
        :return: Exchange name string
        """
        return 'BitMEX'

    def in_message_handler(self, instmt, message):
        """
        Incoming message handler
        :param instmt: Instrument
        :param message: Message
        """
        message = json.loads(message)
        keys = message.keys()
        if 'info' in keys:
            print_log(self.__class__.__name__, message['info'])
        elif 'subscribe' in keys:
            print_log(self.__class__.__name__, 'Subscription of %s is %s' % \
                        (message['request']['args'], \
                         'successful' if message['success'] else 'failed'))
        elif 'table' in keys:
            if message['table'] == 'trade':
                for trade_raw in message['data']:
                    if trade_raw["symbol"] == instmt.get_instmt_code():
                        # Filter out the initial subscriptions
                        trade = self.api_socket.parse_trade(instmt, trade_raw)
                        if trade.trade_id != self.last_exch_trade_id:
                            self.db_trade_id += 1
                            self.last_exch_trade_id = trade.trade_id
                            self.db_client.insert(table=self.db_trades_table_name,
                                                  columns=['id']+Trade.columns(),
                                                  values=[self.db_trade_id]+trade.values())
            elif message['table'] == 'orderBook10':
                for data in message['data']:
                    if data["symbol"] == instmt.get_instmt_code():
                        l2depth = self.api_socket.parse_l2_depth(instmt, data)
                        self.db_order_book_id += 1
                        self.db_client.insert(table=self.db_order_book_table_name,
                                              columns=['id']+L2Depth.columns(),
                                              values=[self.db_order_book_id]+l2depth.values())

            else:
                print_log(self.__class__.__name__, json.dumps(message,indent=2))
        else:
            print_log(self.__class__.__name__, " - " + json.dumps(message))

    def start(self, instmt):
        """
        Start the exchange gateway
        :param instmt: Instrument
        :return List of threads
        """
        self.db_order_book_table_name = self.get_order_book_table_name(instmt.get_exchange_name(),
                                                                       instmt.get_instmt_name())
        self.db_trades_table_name = self.get_trades_table_name(instmt.get_exchange_name(),
                                                               instmt.get_instmt_name())
        self.db_order_book_id = self.get_order_book_init(instmt)
        self.db_trade_id, self.last_exch_trade_id = self.get_trades_init(instmt)

        return [self.api_socket.connect(instmt.get_link(),
                                partial(self.in_message_handler, instmt))]
