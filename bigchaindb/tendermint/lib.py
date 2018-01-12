import logging
from collections import namedtuple
from copy import deepcopy
from os import getenv
from uuid import uuid4

import requests

from bigchaindb import backend
from bigchaindb import Bigchain
from bigchaindb.models import Transaction
from bigchaindb.common.exceptions import SchemaValidationError, ValidationError
from bigchaindb.tendermint.utils import encode_transaction
from bigchaindb.tendermint import fastquery


logger = logging.getLogger(__name__)

TENDERMINT_HOST = getenv('TENDERMINT_HOST', 'localhost')
TENDERMINT_PORT = getenv('TENDERMINT_PORT', '46657')
ENDPOINT = 'http://{}:{}/'.format(TENDERMINT_HOST, TENDERMINT_PORT)


class BigchainDB(Bigchain):

    def post_transaction(self, transaction, mode):
        """Submit a valid transaction to the mempool."""
        mode_list = ('broadcast_tx_async',
                     'broadcast_tx_sync',
                     'broadcast_tx_commit')
        if not mode or mode['mode'] not in mode_list:
            raise ValidationError(('Mode must be one of the following {}.')
                                  .format(', '.join(mode_list)))

        payload = {
            'method': mode['mode'],
            'jsonrpc': '2.0',
            'params': [encode_transaction(transaction.to_dict())],
            'id': str(uuid4())
        }
        # TODO: handle connection errors!
        requests.post(ENDPOINT, json=payload)

    def write_transaction(self, transaction, **mode):
        # This method offers backward compatibility with the Web API.
        """Submit a valid transaction to the mempool."""
        self.post_transaction(transaction, mode)

    def store_transaction(self, transaction):
        """Store a valid transaction to the transactions collection."""

        transaction = deepcopy(transaction.to_dict())
        if transaction['operation'] == 'CREATE':
            asset = transaction.pop('asset')
            asset['id'] = transaction['id']
            if asset['data']:
                backend.query.store_asset(self.connection, asset)

        metadata = transaction.pop('metadata')
        transaction_metadata = {'id': transaction['id'],
                                'metadata': metadata}

        backend.query.store_metadata(self.connection, [transaction_metadata])

        return backend.query.store_transaction(self.connection, transaction)

    def get_transaction(self, transaction_id, include_status=False):
        transaction = backend.query.get_transaction(self.connection, transaction_id)
        asset = backend.query.get_asset(self.connection, transaction_id)
        metadata = backend.query.get_metadata(self.connection, [transaction_id])

        if transaction:
            if asset:
                transaction['asset'] = asset
            else:
                transaction['asset'] = {'data': None}

            if 'metadata' not in transaction:
                metadata = metadata[0] if metadata else None
                if metadata:
                    metadata = metadata.get('metadata')

                transaction.update({'metadata': metadata})

            transaction = Transaction.from_dict(transaction)

        if include_status:
            return transaction, self.TX_VALID if transaction else None
        else:
            return transaction

    def get_spent(self, txid, output):
        transaction = backend.query.get_spent(self.connection, txid,
                                              output)
        if transaction and transaction['operation'] == 'CREATE':
            asset = backend.query.get_asset(self.connection, transaction['id'])

            if asset:
                transaction['asset'] = asset
            else:
                transaction['asset'] = {'data': None}

            return Transaction.from_dict(transaction)
        elif transaction and transaction['operation'] == 'TRANSFER':
            return Transaction.from_dict(transaction)
        else:
            return None

    def store_block(self, block):
        """Create a new block."""

        return backend.query.store_block(self.connection, block)

    def get_latest_block(self):
        """Get the block with largest height."""

        return backend.query.get_latest_block(self.connection)

    def validate_transaction(self, tx):
        """Validate a transaction against the current status of the database."""

        transaction = tx

        if not isinstance(transaction, Transaction):
            try:
                transaction = Transaction.from_dict(tx)
            except SchemaValidationError as e:
                logger.warning('Invalid transaction schema: %s', e.__cause__.message)
                return False
            except ValidationError as e:
                logger.warning('Invalid transaction (%s): %s', type(e).__name__, e)
                return False
        try:
            return transaction.validate(self)
        except ValidationError as e:
            logger.warning('Invalid transaction (%s): %s', type(e).__name__, e)
            return False
        return transaction

    @property
    def fastquery(self):
        return fastquery.FastQuery(self.connection, self.me)


Block = namedtuple('Block', ('app_hash', 'height'))
