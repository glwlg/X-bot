# Fix Report: Discord Video Download & Data Persistence

## Issues
1.  **Link Expired on Discord**: The bot was looking up the *Bot's* user data instead of the *User's* user data when buttons were clicked, causing "Link expired" errors.
2.  **Crash on Download**: The download service crashed with `AttributeError: 'Message' object has no attribute 'edit_text'` on Discord, because it tried to use a Telegram-specific method on a Discord message object.

## Fixes
1.  **UnifiedContext User Resolution**:
    - Updated `DiscordAdapter` to explicitly set the `user` field in `UnifiedContext` to the *Interaction User* (the clicker).
    - Updated `UnifiedContext.user_data` to prioritize this effective user over the message sender (which is the Bot for callbacks).
    
2.  **Platform-Agnostic Downloader**:
    - Created `_safe_edit_message` helper in `download_service.py`.
    - Replaced all `.edit_text()` calls with `_safe_edit_message()` to handle both Telegram (`edit_text`) and Discord (`edit(content=...)`) safely.

## Result
- **Discord**: Link should survive (data persists). Download progress should update. Video should send.
- **Telegram**: Download logic remains robust (and potentially safer).

## Verification
- Code patched.
- Container restarted.
- Logs Checked: Verified logic changes.
