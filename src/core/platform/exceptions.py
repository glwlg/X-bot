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


class MediaProcessingError(PlatformException):
    """Raised when media input cannot be consumed consistently across platforms."""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class UnsupportedMediaOnPlatformError(MediaProcessingError):
    """Raised when a media type is unsupported on the current platform."""

    def __init__(self, message: str):
        super().__init__("unsupported_media_on_platform", message)


class MediaDownloadUnavailableError(MediaProcessingError):
    """Raised when media exists but cannot be downloaded from the platform."""

    def __init__(self, message: str):
        super().__init__("media_download_unavailable", message)
