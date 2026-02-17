# DingTalk (钉钉) Stream Mode Integration Plan

## 1. Overview
This document outlines the detailed plan to integrate DingTalk (钉钉) into the X-Bot platform using the **Stream Mode SDK** (`dingtalk-stream`). This approach uses a WebSocket connection, avoiding the need for a public IP or extensive firewall configuration, making it ideal for the current Docker-based architecture.

## 2. Prerequisites

### 2.1 Dependencies
The project must include the official DingTalk Stream SDK:
```bash
uv add dingtalk-stream
```

### 2.2 Environment Variables
The `.env` file requires the following credentials (obtained from the DingTalk Developer Portal):
```properties
# DingTalk Stream Mode Configuration
DINGTALK_CLIENT_ID=your_app_key_or_suite_key
DINGTALK_CLIENT_SECRET=your_app_secret_or_suite_secret
```

## 3. Architecture Design

The integration adheres to the `BotAdapter` interface defined in `src/core/platform/adapter.py`.

### 3.1 Directory Structure (`src/platforms/dingtalk/`)
```
src/platforms/dingtalk/
├── __init__.py
├── adapter.py       # Core adapter logic (BotAdapter implementation)
├── mapper.py        # Maps DingTalk messages -> UnifiedMessage
└── formatter.py     # Adapts generic Markdown -> DingTalk Markdown
```

### 3.2 Component Breakdown

#### A. `DingTalkAdapter` (`adapter.py`)
This is the heart of the integration. It wraps the `dingtalk_stream.DingTalkStreamClient`.

**Key Responsibilities:**
1.  **Lifecycle Management**:
    -   `start()`: Must use `asyncio.create_task(self.client.start())` to run the client loop without blocking the main X-Bot event loop.
    -   `stop()`: Call `self.client.stop()` (or close connection).

2.  **Command Handling (`on_command`)**:
    -   Unlike Discord, DingTalk Stream doesn't have a "Slash Command Sync" API.
    -   **Approach**: Maintain an internal `self._command_handlers` dictionary.
    -   In `_handle_incoming_network_message`, check if `text` starts with `/`. If it matches a registered command, invoke the handler.

3.  **Callback Handling (`on_callback_query`)**:
    -   Used for interactive card buttons.
    -   **Approach**: Register a specific callback type with DingTalk SDK for "interactive card events". 
    -   Parse the payload (`custom_id` equivalent) and route to handlers registered via `on_callback_query`.

4.  **UI/Button Rendering**:
    -   Implement `_render_card(ui)` to convert the generic `ui` dictionary (used by X-Bot) into DingTalk's **ActionCard** or **Interactive Card** JSON format.
    -   *Note*: DingTalk buttons function differently; we will map `actions` to button links or callbacks.

5.  **Proactive Messaging (`send_message`)**:
    -   Implement fetching logic to send messages to users/groups by ID (needed for scheduler pushes).

#### B. `Message Mapper` (`mapper.py`)
Converts `dingtalk_stream.ChatbotMessage` to `UnifiedMessage`.

| DingTalk Field | UnifiedMessage Field | Note |
| :--- | :--- | :--- |
| `msg.senderId` | `user.id` | StaffId or UnionId |
| `msg.senderNick` | `user.full_name` | User's Name |
| `msg.conversationId` | `chat.id` | Single Chat ID or Group Chat ID |
| `msg.msgType` | `type` | Mapped to `MessageType.TEXT`, etc. |

#### C. `Message Formatter` (`formatter.py`)
DingTalk supports a strict subset of Markdown. The formatter must:
-   Convert standard Markdown links `[text](url)` (DingTalk supports this).
-   Handle Headers (`#`).
-   **Critical**: Strip or Convert Tables (DingTalk doesn't support Markdown tables; they render as raw text). The formatter should convert tables to a list format or code block.
-   Handle Mentions (Need special `@phone` syntax if we want to support it, or just use text).

## 4. Integration Steps

### Step 1: Install Dependencies
Run: `uv add dingtalk-stream`

### Step 2: Implement `formatter.py`
Create `src/platforms/dingtalk/formatter.py`:
```python
def markdown_to_dingtalk_compat(text: str) -> str:
    # 1. Convert tables to code blocks (like Discord formatter)
    # 2. Ensure links are properly formatted
    # 3. Handle bold/italic syntax differences if any
    pass
```

### Step 3: Implement `mapper.py`
Create `src/platforms/dingtalk/mapper.py` to handle the conversion logic.

### Step 4: Implement `adapter.py`
Create `src/platforms/dingtalk/adapter.py`.
-   **Class**: `DingTalkAdapter(BotAdapter)`
-   **Init**: Load credentials, initialize `DingTalkStreamClient`.
-   **Register Handler**: `client.register_callback_handler(ChatbotMessage.TOPIC, self.handler)`
-   **Handler Logic**:
    ```python
    async def handler(self, incoming_ctx):
        # 1. Map to UnifiedMessage
        # 2. Check for Command (e.g. "/start") -> Dispatch
        # 3. Else -> Dispatch to Message Handler
    ```

### Step 5: Update `src/main.py`
Modify `src/main.py` to register the new adapter.

```python
# ... imports
from core.config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET
from platforms.dingtalk.adapter import DingTalkAdapter

# ... inside main() function ...

    # C. DingTalk Adapter
    if DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET:
        dt_adapter = DingTalkAdapter(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET)
        adapter_manager.register_adapter(dt_adapter)
        logger.info("✅ DingTalk Adapter enabled.")
        
        # Register simplified text-based command/message router if needed
        # (The adapter's on_command handles the routing internally)
    else:
        logger.info("ℹ️ DingTalk Adapter skipped (missing credentials).")
```

## 5. Specific Implementation Details (The "Gotchas")

### 5.1 Callback & Interactive Cards
DingTalk "Stream Mode" receives card interactions as a specific event type.
-   We need to ensure the `DingTalkAdapter` listens for the correct TOPIC (check SDK docs: likely `GraphAPI.TOPIC` or `CardCallback.TOPIC` in addition to `ChatbotMessage.TOPIC`).
-   Buttons in DingTalk are often links. For internal callbacks, we might need to use "dtmd" links or specific card Action callbacks. **Initial Strategy**: Use **ActionCards** with button usage logic similar to Telegram's `callback_data`.

### 5.2 Threading & Async
The `dingtalk-stream` SDK allows `await client.start()`. We must **NOT** `await` it directly in `main.py` because it blocks.
**Solution**:
```python
# Inside DingTalkAdapter.start()
import asyncio
asyncio.create_task(self.client.start())
```

### 5.3 Formatting
DingTalk is very picky about Markdown.
-   **No HTML**: Unlike Telegram `reply_text(parse_mode='HTML')`, DingTalk only likes Markdown.
-   **Images**: `reply_photo` needs to send a separate message type (`msgtype="image"`) or use a Card with a top image.

## 6. Verification Plan
1.  **Start-up**: Verify `main.py` starts without error and logs "DingTalk Adapter enabled".
2.  **Health Check**: Send `/start` to the bot in DingTalk and verify:
    -   Log receipt in `log_update` (if mapped).
    -   Bot replies with the menu.
3.  **Command Test**: Test `/new`, `/help`.
4.  **Formatting Test**: Send a message with Markdown content (e.g., `/help`) and verify it doesn't break rendering.
