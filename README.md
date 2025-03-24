# Aether-UI: Cross-Chain Bridge Oracle Simulator

This repository contains a Python-based simulation of a cross-chain bridge oracle. It is designed to demonstrate the architecture and core logic of a service that listens for events on one blockchain (the source chain) and triggers corresponding actions on another (the destination chain). This specific implementation simulates a "lock-and-mint" style bridge.

## Concept

In a decentralized ecosystem with multiple blockchains, cross-chain bridges are essential for transferring assets and data. A common bridge model is "lock-and-mint":

1.  **Lock/Deposit:** A user deposits an asset (e.g., ERC20 token) into a smart contract on the source chain (e.g., Ethereum).
2.  **Event Emission:** The source chain contract emits an event (e.g., `AssetDeposited`) containing details of the deposit (recipient address, amount).
3.  **Oracle Listening:** A network of off-chain services, known as oracles or validators, listens for these events.
4.  **Verification:** After waiting for a certain number of block confirmations to ensure the deposit is final and not part of a chain reorganization, the oracles agree on the event's validity.
5.  **Mint/Release:** An oracle submits a transaction to a smart contract on the destination chain (e.g., Polygon), authorizing it to mint a corresponding wrapped asset (e.g., wERC20) and send it to the user's recipient address.

This script simulates the role of a single oracle (Step 3 to 5), providing a robust foundation for building a real-world, decentralized bridge validator.

## Code Architecture

The script is designed with a clear separation of concerns, using distinct classes for different responsibilities. This makes the code modular, testable, and easier to maintain.

-   `ConfigManager`:
    -   **Responsibility**: Loads all necessary configuration from a `.env` file.
    -   **Details**: It fetches RPC URLs, contract addresses, the oracle's private key, and operational parameters like block confirmations. It includes validation to ensure all critical settings are present at startup.

-   `BlockchainConnector`:
    -   **Responsibility**: Manages the connection to a blockchain via a Web3 provider.
    -   **Details**: It abstracts the `Web3.py` connection logic. It is instantiated separately for the source and destination chains. It also handles common requirements like injecting Proof-of-Authority (PoA) middleware for testnets.

-   `EventScanner`:
    -   **Responsibility**: Scans the source blockchain for new smart contract events.
    -   **Details**: It keeps track of the last block it scanned in a state file (`last_scanned_block.json`) to prevent re-processing events and ensure continuity if the script restarts. It scans a range of blocks from the last scanned block up to the latest block minus a confirmation delay.

-   `TransactionProcessor`:
    -   **Responsibility**: Constructs, signs, and sends transactions to the destination chain.
    -   **Details**: It takes a processed event from the `EventScanner`, formats the data for the destination contract's `mint` function, manages the account's nonce to prevent transaction failures, and submits the transaction to the network.

-   `BridgeOracle`:
    -   **Responsibility**: The main orchestrator class that ties everything together.
    -   **Details**: It initializes all the other components and runs the main application loop. The loop periodically instructs the `EventScanner` to check for new events and, if any are found, passes them to the `TransactionProcessor` for handling.

## How it Works

The operational flow of the script is as follows:

1.  **Initialization**: On startup, the `BridgeOracle` class is instantiated.
    -   The `ConfigManager` loads and validates the environment configuration.
    -   Two `BlockchainConnector` instances are created: one for the source chain and one for the destination chain.
    -   The `EventScanner` is initialized with the source chain connector and contract details.
    -   The `TransactionProcessor` is initialized with the destination chain connector and the oracle's signing key.

2.  **Main Loop**: The `run()` method starts an infinite loop.

3.  **Scanning**: In each iteration, the `EventScanner.scan_for_events()` method is called.
    -   It reads the `last_scanned_block.json` file to know where to start scanning from.
    -   It determines the `to_block` by taking the latest block number from the source chain and subtracting the `REQUIRED_CONFIRMATIONS` value. This prevents acting on events that might be reverted in a chain reorg.
    -   It queries the source chain RPC for `AssetDeposited` events within this block range.

4.  **Processing**: If the scanner returns new events:
    -   The script iterates through each event.
    -   For each event, it calls `TransactionProcessor.process_deposit_event()`.
    -   The processor checks if the corresponding source transaction has already been processed (a safeguard against replays).
    -   It builds a `mint` transaction, signs it with the oracle's private key, and sends it to the destination chain.
    -   It logs the resulting transaction hash.

5.  **State Update**: After scanning, the `EventScanner` updates the `last_scanned_block.json` file with the `to_block` number, ensuring the next cycle starts where the last one finished.

6.  **Wait**: The script then pauses for the duration specified by `SCAN_INTERVAL_SECONDS` before starting the loop again.

## Usage Example

### 1. Prerequisites

-   Python 3.8+
-   Access to RPC endpoints for two EVM-compatible blockchains (e.g., from Infura, Alchemy, or a local node).
-   An account with funds on the destination chain to pay for gas fees.

### 2. Setup

-   Clone the repository:
    ```bash
    git clone <repository_url>
    cd aether-ui
    ```

-   Create a virtual environment and install dependencies:
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    pip install -r requirements.txt
    ```

-   Create a `.env` file in the root directory by copying the example and filling in the values:
    ```env
    # --- SOURCE CHAIN (e.g., Ethereum Sepolia) ---
    SOURCE_CHAIN_RPC="https://sepolia.infura.io/v3/YOUR_INFURA_PROJECT_ID"
    SOURCE_BRIDGE_CONTRACT_ADDRESS="0x..._Source_Contract_Address_..."

    # --- DESTINATION CHAIN (e.g., Polygon Mumbai) ---
    DEST_CHAIN_RPC="https://polygon-mumbai.g.alchemy.com/v2/YOUR_ALCHEMY_API_KEY"
    DEST_BRIDGE_CONTRACT_ADDRESS="0x..._Destination_Contract_Address_..."

    # --- ORACLE CONFIGURATION ---
    # IMPORTANT: Do NOT commit this file with a real private key. Use a dedicated, low-value account for testing.
    ORACLE_PRIVATE_KEY="your_oracle_account_private_key_without_0x"

    # --- OPERATIONAL PARAMETERS ---
    # Number of blocks to wait on the source chain before considering an event confirmed
    REQUIRED_CONFIRMATIONS=12
    # Time in seconds between each scan cycle
    SCAN_INTERVAL_SECONDS=30
    # File to store the last processed block number
    STATE_FILE="last_scanned_block.json"
    ```

### 3. Run the Oracle

-   Start the script from your terminal:
    ```bash
    python script.py
    ```

-   The oracle will start running. You will see log output in your console:
    ```
    2023-10-27 14:30:00 - INFO - [config_manager.validate_config] - Configuration loaded and validated successfully.
    2023-10-27 14:30:01 - INFO - [blockchain_connector._connect] - Connecting to blockchain via RPC: https://sepolia.infura.io/v3... 
    2023-10-27 14:30:02 - INFO - [blockchain_connector._connect] - Successfully connected. Chain ID: 11155111
    2023-10-27 14:30:02 - INFO - [blockchain_connector._connect] - Connecting to blockchain via RPC: https://polygon-mumbai.g.alche...
    2023-10-27 14:30:03 - INFO - [blockchain_connector._connect] - Successfully connected. Chain ID: 80001
    2023-10-27 14:30:03 - INFO - [transaction_processor.__init__] - Transaction processor initialized for account: 0xYourOracleAccountAddress
    2023-10-27 14:30:03 - INFO - [__main__.run] - Aether-UI Bridge Oracle starting up...
    2023-10-27 14:30:03 - INFO - [__main__.run] - Watching for 'AssetDeposited' events on contract 0x..._Source_Contract_Address_...
    2023-10-27 14:30:03 - INFO - [__main__.run] - Will submit 'mint' transactions to 0x..._Destination_Contract_Address_...
    2023-10-27 14:30:04 - INFO - [event_scanner.scan_for_events] - Scanning for 'AssetDeposited' events from block 4850123 to 4850220...
    2023-10-27 14:30:05 - INFO - [event_scanner.scan_for_events] - Found 1 new 'AssetDeposited' events.
    2023-10-27 14:30:05 - INFO - [__main__.run] - Processing 1 new events.
    2023-10-27 14:30:05 - INFO - [transaction_processor.process_deposit_event] - Processing deposit: 1000000000000000000 tokens for 0x...UserAddress... from source tx 0x...source_tx_hash...
    2023-10-27 14:30:06 - INFO - [transaction_processor.process_deposit_event] - Submitted mint transaction to destination chain. Tx Hash: 0x...destination_tx_hash...
    2023-10-27 14:30:35 - INFO - [__main__.run] - No new events found in this cycle.
    ```
