# Fix Report: Discord Buttons & Callbacks

## Issue
User reported "No operation options" when a video link was detected. This was caused by DiscordAdapter ignoring `reply_markup` (Telegram Inline Keyboard) and lacking callback handling logic.

## Fix Implemented
1.  **DiscordAdapter Update**:
    - Added `_telegram_markup_to_discord_view` to convert Inline keyboards to Discord UI Views/Buttons.
    - Added `_generic_button_callback` to handle button clicks (Interactions).
    - Updated `reply_text` and `edit_text` to render these Views.
    - Added `on_callback_query` registration method.

2.  **Unified Context Update**:
    - Added `callback_data` property to `UnifiedContext` to abstract `update.callback_query.data` vs `interaction.data['custom_id']`.
    - Added `answer_callback` method to abstract acknowledgement (`answer` vs `defer`).

3.  **Handler Refactoring**:
    - Updated `media_handlers.py` (`handle_video_actions`, `handle_large_file_actions`) to use the new Unified Context methods.
    - Updated `main.py` to register callback handlers (`handle_video_actions`, `handle_skill_callback`) for the Discord adapter.

## Result
- Buttons "Download" and "Summarize" should now appear on Discord.
- Clicking them should trigger the correct actions.

## Verification
- Container restarted successfully.
- Logs are clean.
