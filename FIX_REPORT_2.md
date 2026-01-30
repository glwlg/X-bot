# Fix Report: Discord Callback TypeError

## Issue
User encountered `TypeError: UnifiedContext.__init__() got an unexpected keyword argument 'platform_context'` when clicking buttons on Discord.

## Fix Implemented
1.  **DiscordAdapter**: Removed the invalid `platform_context` argument from the `UnifiedContext` constructor call in `_generic_button_callback`.
2.  **Code Cleanup**: Removed a duplicate initialization of `self._message_handler`.

## Result
Typo corrected. Button callbacks should now instantiate `UnifiedContext` correctly and proceed to the handler logic.

## Verification
- Code patched.
- Container restarted.
