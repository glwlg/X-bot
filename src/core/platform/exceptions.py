class PlatformException(Exception):
    """Base exception for platform-related errors"""
    pass

class MessageEditNotSupported(PlatformException):
    """Raised when editing a message is not supported by the platform"""
    pass

class MessageSendError(PlatformException):
    """Raised when sending a message fails"""
    pass

class UserNotFound(PlatformException):
    """Raised when a user cannot be found on the platform"""
    pass
