from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os

import magic
from google.protobuf.message import DecodeError
from PIL import Image

from ._binder import free_bytes
from .exc import SendMessageError
from .proto.Neonize_pb2 import SendMessageReturnFunction, SendResponse, UploadResponse
from .proto.waE2E.WAWebProtobufsE2E_pb2 import (
    AudioMessage,
    DocumentMessage,
    ImageMessage,
    Message,
    StickerMessage,
    VideoMessage,
)
from .utils.calc import AspectRatioMethod
from .utils.enum import MediaType
from .utils.ffmpeg import FFmpeg
from .utils.sticker import convert_to_webp


_MEDIA_TYPES = {
    "image": MediaType.MediaImage,
    "video": MediaType.MediaVideo,
    "animation": MediaType.MediaVideo,
    "audio": MediaType.MediaAudio,
    "voice": MediaType.MediaAudio,
    "document": MediaType.MediaDocument,
    "sticker": MediaType.MediaImage,
}


@dataclass(frozen=True)
class PreparedNewsletterMedia:
    data: bytes
    kind: str
    media_type: MediaType
    mimetype: str
    width: int = 0
    height: int = 0
    seconds: int = 0
    thumbnail: bytes = b""
    animated: bool = False


def parse_send_response(bytes_ptr, message: Message) -> SendResponse:
    """Decode a native send result and reject missing/empty native responses."""
    if not bytes_ptr:
        raise SendMessageError("native send returned no response pointer")
    try:
        protobytes = bytes_ptr.contents.get_bytes()
    finally:
        free_bytes(bytes_ptr)
    if not protobytes:
        raise SendMessageError("native send returned an empty response")
    try:
        model = SendMessageReturnFunction.FromString(protobytes)
    except DecodeError as exc:
        raise SendMessageError("native send returned an invalid response") from exc
    if model.Error:
        raise SendMessageError(model.Error)
    if not model.HasField("SendResponse"):
        raise SendMessageError("native send response is missing SendResponse")
    model.SendResponse.MergeFrom(model.SendResponse.__class__(Message=message))
    return model.SendResponse


def _image_preview(data: bytes) -> tuple[int, int, bytes]:
    with Image.open(BytesIO(data)) as image:
        width, height = image.size
        preview = image.copy()
        preview.thumbnail(AspectRatioMethod(width, height, res=200))
        if preview.mode != "RGB":
            preview = preview.convert("RGB")
        thumbnail = BytesIO()
        preview.save(thumbnail, format="jpeg")
    return width, height, thumbnail.getvalue()


def _video_metadata(data: bytes) -> tuple[int, int, int, bytes]:
    with FFmpeg(data) as ffmpeg:
        info = ffmpeg.extract_info()
        thumbnail = ffmpeg.extract_thumbnail()
    video_stream = next((stream for stream in info.streams if stream.codec_type == "video"), None)
    width = int(video_stream.width or 0) if video_stream else 0
    height = int(video_stream.height or 0) if video_stream else 0
    return width, height, int(info.format.duration or 0), thumbnail


def prepare_newsletter_media(
    data: bytes,
    media_kind: str,
    media_type: MediaType,
) -> PreparedNewsletterMedia:
    kind = media_kind.strip().lower()
    expected_type = _MEDIA_TYPES.get(kind)
    if expected_type is None:
        raise ValueError(f"unsupported newsletter media kind: {media_kind}")
    if media_type is not expected_type:
        raise ValueError(
            f"newsletter media kind {kind} requires {expected_type.name}, got {media_type.name}"
        )
    if not isinstance(data, bytes) or not data:
        raise ValueError("newsletter media must be non-empty bytes")

    prepared_data = data
    animated = False
    if kind == "animation" and magic.from_buffer(data, mime=True) == "image/gif":
        with FFmpeg(data) as ffmpeg:
            prepared_data = ffmpeg.gif_to_mp4()
    elif kind == "sticker":
        is_webp = magic.from_buffer(data, mime=True) == "image/webp"
        prepared_data, animated = convert_to_webp(
            data,
            "",
            "",
            passthrough=is_webp,
        )

    mimetype = magic.from_buffer(prepared_data, mime=True)
    width = height = seconds = 0
    thumbnail = b""
    if kind == "image":
        width, height, thumbnail = _image_preview(prepared_data)
    elif kind in {"video", "animation"}:
        width, height, seconds, thumbnail = _video_metadata(prepared_data)
    elif kind in {"audio", "voice"}:
        with FFmpeg(prepared_data) as ffmpeg:
            seconds = int(ffmpeg.extract_info().format.duration or 0)
    elif kind == "sticker":
        with Image.open(BytesIO(prepared_data)) as sticker:
            width, height = sticker.size
            animated = animated or bool(getattr(sticker, "is_animated", False))

    return PreparedNewsletterMedia(
        data=prepared_data,
        kind=kind,
        media_type=media_type,
        mimetype=mimetype,
        width=width,
        height=height,
        seconds=seconds,
        thumbnail=thumbnail,
        animated=animated,
    )


def prepare_newsletter_media_file(
    path: str,
    media_kind: str,
    media_type: MediaType,
    mimetype: str,
) -> PreparedNewsletterMedia:
    """Prepare metadata for a file-backed newsletter video or document."""
    kind = media_kind.strip().lower()
    if kind not in {"video", "document"}:
        raise ValueError("file-backed newsletter media must be video or document")
    expected_type = _MEDIA_TYPES[kind]
    if media_type is not expected_type:
        raise ValueError(
            f"newsletter media kind {kind} requires {expected_type.name}, got {media_type.name}"
        )
    if not isinstance(path, str) or not os.path.isfile(path):
        raise ValueError("newsletter media path must be a local regular file")
    width = height = seconds = 0
    thumbnail = b""
    if kind == "video":
        with FFmpeg(path) as ffmpeg:
            info = ffmpeg.extract_info()
            thumbnail = ffmpeg.extract_thumbnail()
        video_stream = next((stream for stream in info.streams if stream.codec_type == "video"), None)
        width = int(video_stream.width or 0) if video_stream else 0
        height = int(video_stream.height or 0) if video_stream else 0
        seconds = int(info.format.duration or 0)
    return PreparedNewsletterMedia(
        data=b"",
        kind=kind,
        media_type=media_type,
        mimetype=mimetype,
        width=width,
        height=height,
        seconds=seconds,
        thumbnail=thumbnail,
    )


def build_newsletter_media_message(
    prepared: PreparedNewsletterMedia,
    upload: UploadResponse,
    caption: str = "",
    filename: str = "",
) -> Message:
    common = {
        "URL": upload.url,
        "directPath": upload.DirectPath,
        "fileLength": upload.FileLength,
        "fileSHA256": upload.FileSHA256,
        "mimetype": prepared.mimetype,
    }
    if prepared.kind == "image":
        return Message(
            imageMessage=ImageMessage(
                **common,
                caption=caption,
                width=prepared.width,
                height=prepared.height,
                JPEGThumbnail=prepared.thumbnail,
            )
        )
    if prepared.kind in {"video", "animation"}:
        return Message(
            videoMessage=VideoMessage(
                **common,
                caption=caption,
                width=prepared.width,
                height=prepared.height,
                seconds=prepared.seconds,
                JPEGThumbnail=prepared.thumbnail,
                gifPlayback=prepared.kind == "animation",
            )
        )
    if prepared.kind in {"audio", "voice"}:
        return Message(
            audioMessage=AudioMessage(
                **common,
                seconds=prepared.seconds,
                PTT=prepared.kind == "voice",
            )
        )
    if prepared.kind == "document":
        return Message(
            documentMessage=DocumentMessage(
                **common,
                caption=caption,
                fileName=filename,
            )
        )
    return Message(
        stickerMessage=StickerMessage(
            **common,
            width=prepared.width,
            height=prepared.height,
            isAnimated=prepared.animated,
        )
    )
