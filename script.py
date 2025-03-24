import os
import time
import json
import logging
from typing import Dict, Any, Optional, List

from web3 import Web3
from web3.middleware import geth_poa_middleware
from web3.exceptions import TransactionNotFound, BlockNotFound
from dotenv import load_dotenv
import requests

# --- Configuration Setup ---
# Load environment variables from a .env file for secure configuration management.
load_dotenv()

# --- Basic Logging Configuration ---
# Provides clear, time-stamped output for monitoring the service's operations.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(module)s.%(funcName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class ConfigManager:
    """
    Manages the application's configuration, loading necessary parameters from environment variables.
    This class centralizes configuration access and provides validation for essential settings.
    """
    def __init__(self):
        logging.info("Initializing configuration manager...")
        self.source_chain_rpc: Optional[str] = os.getenv("SOURCE_CHAIN_RPC")
        self.dest_chain_rpc: Optional[str] = os.getenv("DEST_CHAIN_RPC")
        self.source_bridge_contract_address: Optional[str] = os.getenv("SOURCE_BRIDGE_CONTRACT_ADDRESS")
        self.dest_bridge_contract_address: Optional[str] = os.getenv("DEST_BRIDGE_CONTRACT_ADDRESS")
        self.oracle_private_key: Optional[str] = os.getenv("ORACLE_PRIVATE_KEY")
        self.required_confirmations: int = int(os.getenv("REQUIRED_CONFIRMATIONS", 12))
        self.scan_interval_seconds: int = int(os.getenv("SCAN_INTERVAL_SECONDS", 15))
        self.state_file: str = os.getenv("STATE_FILE", "last_scanned_block.json")
        self.validate_config()

    def validate_config(self) -> None:
        """
        Validates that all required environment variables are set. 
        Raises a ValueError if a critical configuration is missing.
        """
        required_vars = [
            self.source_chain_rpc, self.dest_chain_rpc,
            self.source_bridge_contract_address, self.dest_bridge_contract_address,
            self.oracle_private_key
        ]
        if any(var is None for var in required_vars):
            logging.error("One or more critical environment variables are not set.")
            raise ValueError("Missing required environment variables. Please check your .env file.")
        logging.info("Configuration loaded and validated successfully.")


class BlockchainConnector:
    """
    Handles the connection to a specific blockchain via its RPC endpoint.
    This class abstracts the Web3.py connection logic, including handling specific chain middlewares
    like PoA (Proof of Authority) which is common in testnets and sidechains.
    """
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self.web3 = self._connect()

    def _connect(self) -> Web3:
        """
        Establishes a connection to the blockchain node.
        Injects PoA middleware if the chain appears to require it (based on `geth_poa_middleware` logic).
        Includes robust error handling for connection issues.
        """
        try:
            logging.info(f"Connecting to blockchain via RPC: {self.rpc_url[:30]}...")
            web3_instance = Web3(Web3.HTTPProvider(self.rpc_url))
            # Inject middleware for PoA chains like Goerli, Sepolia, or Polygon Mumbai
            web3_instance.middleware_onion.inject(geth_poa_middleware, layer=0)
            if not web3_instance.is_connected():
                raise ConnectionError("Failed to connect to the blockchain node.")
            logging.info(f"Successfully connected. Chain ID: {web3_instance.eth.chain_id}")
            return web3_instance
        except requests.exceptions.ConnectionError as e:
            logging.error(f"RPC connection error for {self.rpc_url}: {e}")
            raise
        except Exception as e:
            logging.error(f"An unexpected error occurred during connection to {self.rpc_url}: {e}")
            raise

    def get_contract(self, address: str, abi: List[Dict[str, Any]]) -> Any:
        """
        Returns a Web3 contract instance for interacting with a smart contract.
        """
        checksum_address = self.web3.to_checksum_address(address)
        return self.web3.eth.contract(address=checksum_address, abi=abi)


class EventScanner:
    """
    Scans the source blockchain for specific events within a given block range.
    It maintains state (last scanned block) to ensure continuous and non-overlapping scanning.
    """
    def __init__(self, connector: BlockchainConnector, contract_address: str, contract_abi: List[Dict[str, Any]], state_file: str):
        self.connector = connector
        self.web3 = connector.web3
        self.contract = connector.get_contract(contract_address, contract_abi)
        self.state_file = state_file

    def _load_last_scanned_block(self) -> int:
        """
        Loads the last scanned block number from the state file.
        If the file doesn't exist, it starts from the current block, preventing a full history scan on first run.
        """
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                return int(state.get('last_scanned_block', self.web3.eth.block_number - 1))
        except (FileNotFoundError, json.JSONDecodeError):
            logging.warning(f"State file '{self.state_file}' not found or invalid. Starting scan from current block.")
            return self.web3.eth.block_number - 1

    def _save_last_scanned_block(self, block_number: int) -> None:
        """
        Saves the latest scanned block number to the state file for persistence.
        """
        with open(self.state_file, 'w') as f:
            json.dump({'last_scanned_block': block_number}, f)

    def scan_for_events(self, event_name: str, required_confirmations: int) -> List[Dict[str, Any]]:
        """ 
        Scans for new events from the last scanned block to the latest confirmed block.
        This is the core logic of the scanner, handling block ranges and event filtering.
        """
        try:
            last_scanned = self._load_last_scanned_block()
            latest_block = self.web3.eth.block_number
            
            # Define the scan range, ensuring we respect the confirmation threshold
            from_block = last_scanned + 1
            to_block = latest_block - required_confirmations

            if from_block > to_block:
                logging.info(f"No new confirmed blocks to scan. Current: {latest_block}, Last Scanned: {last_scanned}")
                return []

            logging.info(f"Scanning for '{event_name}' events from block {from_block} to {to_block}...")
            
            event_filter = self.contract.events[event_name].create_filter(
                fromBlock=from_block,
                toBlock=to_block
            )
            events = event_filter.get_all_entries()
            
            if events:
                logging.info(f"Found {len(events)} new '{event_name}' events.")

            self._save_last_scanned_block(to_block)
            return [dict(event) for event in events] # Convert AttributeDict to standard dict

        except BlockNotFound:
            logging.warning("A block was not found during the scan. This could be due to a chain reorg. Will retry.")
            return []
        except Exception as e:
            logging.error(f"An unexpected error occurred during event scanning: {e}")
            return []


class TransactionProcessor:
    """
    Processes events by creating, signing, and sending transactions to the destination chain.
    It manages the oracle's account, nonce, and gas price estimation.
    """
    def __init__(self, connector: BlockchainConnector, contract_address: str, contract_abi: List[Dict[str, Any]], private_key: str):
        self.connector = connector
        self.web3 = connector.web3
        self.contract = connector.get_contract(contract_address, contract_abi)
        self.private_key = private_key
        self.account = self.web3.eth.account.from_key(private_key)
        logging.info(f"Transaction processor initialized for account: {self.account.address}")

    def process_deposit_event(self, event: Dict[str, Any]) -> Optional[str]:
        """
        Constructs and sends a 'mint' transaction based on a 'Deposit' event from the source chain.
        Handles nonce management and gas estimation for reliable transaction submission.
        """
        try:
            args = event.get('args', {})
            recipient = args.get('recipient')
            amount = args.get('amount')
            source_tx_hash = event.get('transactionHash').hex()

            if not all([recipient, amount, source_tx_hash]):
                logging.warning(f"Malformed event detected, missing required args: {event}")
                return None

            logging.info(f"Processing deposit: {amount} tokens for {recipient} from source tx {source_tx_hash}")

            # Check if this transaction has already been processed to prevent replays
            # In a real system, this would involve checking contract state.
            if self.contract.functions.processedTransactions(source_tx_hash).call():
                logging.warning(f"Transaction {source_tx_hash} has already been processed. Skipping.")
                return None

            nonce = self.web3.eth.get_transaction_count(self.account.address)
            
            # Build the transaction for the 'mint' function
            tx = self.contract.functions.mint(recipient, amount, source_tx_hash).build_transaction({
                'chainId': self.web3.eth.chain_id,
                'gas': 200000, # A safe gas limit; use estimateGas in production
                'gasPrice': self.web3.eth.gas_price,
                'nonce': nonce,
            })

            signed_tx = self.web3.eth.account.sign_transaction(tx, private_key=self.private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            logging.info(f"Submitted mint transaction to destination chain. Tx Hash: {tx_hash.hex()}")
            
            # Wait for transaction receipt (optional, but good for confirmation)
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt['status'] == 1:
                logging.info(f"Mint transaction successful! Block: {receipt['blockNumber']}")
            else:
                logging.error(f"Mint transaction failed! Tx Hash: {tx_hash.hex()}")

            return tx_hash.hex()

        except TransactionNotFound:
            logging.error("Transaction not found after sending. It might have been dropped.")
            return None
        except ValueError as e:
            # Catches issues like insufficient funds or contract reverts
            logging.error(f"Error building or sending transaction: {e}")
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during transaction processing: {e}")
            return None


class BridgeOracle:
    """
    The main orchestrator class.
    It initializes all components (connectors, scanner, processor) and runs the main event loop,
    tying together the entire cross-chain listening and transaction submission process.
    """
    def __init__(self):
        self.config = ConfigManager()
        
        # --- Dummy ABIs for Simulation ---
        # In a real-world scenario, these would be loaded from JSON files.
        self.source_abi = json.loads('[{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"recipient","type":"address"},{"indexed":false,"internalType":"uint256","name":"amount","type":"uint256"}],"name":"AssetDeposited","type":"event"}]')
        self.dest_abi = json.loads('[{"inputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"name":"processedTransactions","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"}, {"inputs":[{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"},{"internalType":"bytes32","name":"sourceTxHash","type":"bytes32"}],"name":"mint","outputs":[],"stateMutability":"nonpayable","type":"function"}]')

        # --- Component Initialization ---
        self.source_connector = BlockchainConnector(self.config.source_chain_rpc)
        self.dest_connector = BlockchainConnector(self.config.dest_chain_rpc)

        self.event_scanner = EventScanner(
            connector=self.source_connector,
            contract_address=self.config.source_bridge_contract_address,
            contract_abi=self.source_abi,
            state_file=self.config.state_file
        )

        self.tx_processor = TransactionProcessor(
            connector=self.dest_connector,
            contract_address=self.config.dest_bridge_contract_address,
            contract_abi=self.dest_abi,
            private_key=self.config.oracle_private_key
        )

    def run(self):
        """
        Starts the main operational loop of the bridge oracle.
        This loop periodically scans for events and processes them.
        """
        logging.info("Aether-UI Bridge Oracle starting up...")
        logging.info(f"Watching for 'AssetDeposited' events on contract {self.config.source_bridge_contract_address}")
        logging.info(f"Will submit 'mint' transactions to {self.config.dest_bridge_contract_address}")

        while True:
            try:
                logging.debug("Starting new scan cycle.")
                # 1. Scan for new deposit events on the source chain
                deposit_events = self.event_scanner.scan_for_events(
                    'AssetDeposited',
                    self.config.required_confirmations
                )
                
                # 2. If events are found, process each one
                if deposit_events:
                    logging.info(f"Processing {len(deposit_events)} new events.")
                    for event in deposit_events:
                        self.tx_processor.process_deposit_event(event)
                else:
                    logging.info("No new events found in this cycle.")

                # 3. Wait for the next interval
                logging.debug(f"Sleeping for {self.config.scan_interval_seconds} seconds...")
                time.sleep(self.config.scan_interval_seconds)

            except KeyboardInterrupt:
                logging.info("Shutdown signal received. Exiting gracefully.")
                break
            except Exception as e:
                logging.critical(f"A critical error occurred in the main loop: {e}. Restarting loop after a delay.")
                time.sleep(self.config.scan_interval_seconds * 2) # Longer sleep after critical failure


if __name__ == "__main__":
    try:
        oracle = BridgeOracle()
        oracle.run()
    except ValueError as e:
        # This will catch configuration validation errors on startup.
        logging.critical(f"Failed to start oracle due to a configuration error: {e}")
    except Exception as e:
        logging.critical(f"An unexpected error prevented the oracle from starting: {e}")
