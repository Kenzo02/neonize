from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from neonize.client import NewClient
from neonize.aioze.client import NewAClient
from neonize.exc import SendMessageError, UploadError
from neonize.newsletter_media import (
    PreparedNewsletterMedia,
    build_newsletter_media_message,
    parse_send_response,
    prepare_newsletter_media,
)
from neonize.proto.Neonize_pb2 import (
    JID,
    MessageDebugTimings,
    SendMessageReturnFunction,
    SendResponse,
    UploadResponse,
)
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message
from neonize.utils.enum import MediaType


def _png(width: int = 640, height: int = 360) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "red").save(output, "PNG")
    return output.getvalue()


def _upload(handle: str = "newsletter-handle") -> UploadResponse:
    return UploadResponse(
        url="https://media.invalid/file",
        DirectPath="/newsletter/file",
        Handle=handle,
        FileSHA256=b"plain-sha",
        FileLength=123,
    )


def test_plain_newsletter_upload_response_serializes_without_encryption_fields():
    encoded = _upload().SerializeToString()
    decoded = UploadResponse.FromString(encoded)

    assert decoded.Handle == "newsletter-handle"
    assert not decoded.HasField("MediaKey")
    assert not decoded.HasField("FileEncSHA256")


def test_send_newsletter_image_uploads_once_and_preserves_original_dimensions():
    client = object.__new__(NewClient)
    client.upload_newsletter = MagicMock(return_value=_upload())
    client.send_message = MagicMock(return_value="sent")
    data = _png()

    result = client.send_newsletter_media(
        JID(User="100", Server="newsletter"),
        data,
        media_kind="image",
        media_type="image/png",
        caption="caption",
        filename="",
    )

    assert result == "sent"
    client.upload_newsletter.assert_called_once_with(data, MediaType.MediaImage)
    message = client.send_message.call_args.args[1]
    assert client.send_message.call_args.kwargs == {"media_handle": "newsletter-handle"}
    assert message.imageMessage.width == 640
    assert message.imageMessage.height == 360
    assert message.imageMessage.caption == "caption"
    assert message.imageMessage.fileSHA256 == b"plain-sha"
    assert not message.imageMessage.HasField("mediaKey")
    assert not message.imageMessage.HasField("fileEncSHA256")


def test_send_newsletter_media_rejects_missing_handle_without_sending():
    client = object.__new__(NewClient)
    client.upload_newsletter = MagicMock(return_value=_upload(handle=""))
    client.send_message = MagicMock()

    with pytest.raises(UploadError, match="missing Handle"):
        client.send_newsletter_media(
            JID(User="100", Server="newsletter"),
            _png(),
            media_kind="image",
            media_type="image/png",
            caption="",
            filename="",
        )

    client.send_message.assert_not_called()


def test_send_newsletter_media_rejects_non_newsletter_before_upload():
    client = object.__new__(NewClient)
    client.upload_newsletter = MagicMock()

    with pytest.raises(ValueError, match="newsletter destination"):
        client.send_newsletter_media(
            JID(User="100", Server="s.whatsapp.net"),
            _png(),
            media_kind="image",
            media_type="image/png",
            caption="",
            filename="",
        )

    client.upload_newsletter.assert_not_called()


def test_media_kind_controls_exact_upload_enum():
    with pytest.raises(ValueError, match="requires MediaVideo"):
        prepare_newsletter_media(_png(), "video", MediaType.MediaImage)


@pytest.mark.parametrize(
    ("kind", "declared_type", "detected_type", "upload_type"),
    [
        ("image", "image/png", "image/png", MediaType.MediaImage),
        ("sticker", "image/webp", "image/webp", MediaType.MediaImage),
        ("video", "video/mp4", "video/mp4", MediaType.MediaVideo),
        ("animation", "image/gif", "video/mp4", MediaType.MediaVideo),
        ("audio", "audio/opus", "audio/ogg", MediaType.MediaAudio),
        ("voice", "audio/ogg", "audio/ogg", MediaType.MediaAudio),
        ("document", "text/csv", "text/plain", MediaType.MediaDocument),
    ],
)
def test_public_newsletter_media_derives_upload_type_from_service_kwargs(
    kind, declared_type, detected_type, upload_type
):
    client = object.__new__(NewClient)
    client.upload_newsletter = MagicMock(return_value=_upload())
    client.send_message = MagicMock(return_value="sent")
    prepared = PreparedNewsletterMedia(
        data=b"prepared",
        kind=kind,
        media_type=upload_type,
        mimetype=detected_type,
    )

    with (
        patch("neonize.client.prepare_newsletter_media", return_value=prepared) as prepare,
        patch("neonize.client.build_newsletter_media_message", return_value=Message()) as build,
    ):
        result = client.send_newsletter_media(
            JID(User="100", Server="newsletter"),
            b"source",
            media_kind=kind,
            media_type=declared_type,
            caption="caption",
            filename="file.bin",
        )

    assert result == "sent"
    prepare.assert_called_once_with(b"source", kind, upload_type)
    client.upload_newsletter.assert_called_once_with(b"prepared", upload_type)
    built_prepared = build.call_args.args[0]
    expected_mimetype = declared_type if kind == "document" else detected_type
    assert built_prepared.mimetype == expected_mimetype
    assert build.call_args.args[1:] == (
        client.upload_newsletter.return_value,
        "caption",
        "file.bin",
    )


def test_newsletter_document_message_preserves_declared_csv_mimetype():
    prepared = PreparedNewsletterMedia(
        data=b"first,second\n1,2\n",
        kind="document",
        media_type=MediaType.MediaDocument,
        mimetype="text/csv",
    )

    message = build_newsletter_media_message(prepared, _upload(), filename="data.csv")

    assert message.documentMessage.mimetype == "text/csv"


def test_animation_converts_gif_before_metadata_and_upload():
    converted = b"converted-mp4"
    probe = SimpleNamespace(
        format=SimpleNamespace(duration=2.9),
        streams=[SimpleNamespace(codec_type="video", width=320, height=240)],
    )
    ffmpeg = MagicMock()
    ffmpeg.__enter__.return_value = ffmpeg
    ffmpeg.gif_to_mp4.return_value = converted
    ffmpeg.extract_info.return_value = probe
    ffmpeg.extract_thumbnail.return_value = b"thumb"

    with (
        patch("neonize.newsletter_media.magic.from_buffer", side_effect=["image/gif", "video/mp4"]),
        patch("neonize.newsletter_media.FFmpeg", return_value=ffmpeg),
    ):
        prepared = prepare_newsletter_media(
            b"gif-data", "animation", MediaType.MediaVideo
        )

    assert prepared.data == converted
    assert prepared.mimetype == "video/mp4"
    assert (prepared.width, prepared.height, prepared.seconds) == (320, 240, 2)
    ffmpeg.gif_to_mp4.assert_called_once_with()


def test_sticker_uses_existing_webp_conversion_for_non_webp_input():
    converted = _png(512, 512)
    with (
        patch("neonize.newsletter_media.magic.from_buffer", side_effect=["image/png", "image/webp"]),
        patch(
            "neonize.newsletter_media.convert_to_webp",
            return_value=(converted, False),
        ) as convert,
    ):
        prepared = prepare_newsletter_media(
            b"source-image", "sticker", MediaType.MediaImage
        )

    convert.assert_called_once_with(b"source-image", "", "", passthrough=False)
    assert prepared.data == converted
    assert prepared.width == 512
    assert prepared.height == 512


@pytest.mark.parametrize(
    ("kind", "field", "expected"),
    [
        ("video", "videoMessage", {"gifPlayback": False}),
        ("animation", "videoMessage", {"gifPlayback": True}),
        ("audio", "audioMessage", {"PTT": False}),
        ("voice", "audioMessage", {"PTT": True}),
        ("document", "documentMessage", {"fileName": "file.bin"}),
        ("sticker", "stickerMessage", {"isAnimated": True}),
    ],
)
def test_build_newsletter_message_for_supported_kinds(kind, field, expected):
    prepared = PreparedNewsletterMedia(
        data=b"data",
        kind=kind,
        media_type=MediaType.MediaDocument,
        mimetype="application/octet-stream",
        width=10,
        height=20,
        seconds=3,
        animated=True,
    )

    message = build_newsletter_media_message(
        prepared, _upload(), caption="caption", filename="file.bin"
    )
    payload = getattr(message, field)

    for name, value in expected.items():
        assert getattr(payload, name) == value
    assert payload.fileSHA256 == b"plain-sha"
    assert not payload.HasField("mediaKey")
    assert not payload.HasField("fileEncSHA256")


def test_sync_send_message_preserves_old_abi_and_uses_additive_handle_export():
    client = object.__new__(NewClient)
    client.uuid = b"client"
    native = MagicMock()
    client._NewClient__client = native
    to = JID(User="100", Server="newsletter", RawAgent=0, Device=0, Integrator=0)
    message = Message(conversation="hello")

    with patch("neonize.client.parse_send_response", return_value="old"):
        assert client.send_message(to, message) == "old"
    native.SendMessage.assert_called_once()
    native.SendMessageWithMediaHandle.assert_not_called()

    native.reset_mock()
    with patch("neonize.client.parse_send_response", return_value="new"):
        assert client.send_message(to, message, media_handle="handle") == "new"
    native.SendMessage.assert_not_called()
    assert native.SendMessageWithMediaHandle.call_args.args[-1] == b"handle"


def test_parse_send_response_rejects_empty_native_buffer():
    pointer = MagicMock()
    pointer.contents.get_bytes.return_value = b""

    with (
        patch("neonize.newsletter_media.free_bytes") as free,
        pytest.raises(SendMessageError, match="empty response"),
    ):
        parse_send_response(pointer, Message())

    free.assert_called_once_with(pointer)


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (b"\xff", "invalid response"),
        (SendMessageReturnFunction(Error="provider rejected").SerializeToString(), "provider rejected"),
        (b"\x18\x01", "missing SendResponse"),
    ],
)
def test_parse_send_response_rejects_invalid_provider_and_missing_results(payload, error):
    pointer = MagicMock()
    pointer.contents.get_bytes.return_value = payload

    with (
        patch("neonize.newsletter_media.free_bytes") as free,
        pytest.raises(SendMessageError, match=error),
    ):
        parse_send_response(pointer, Message())

    free.assert_called_once_with(pointer)


def test_parse_send_response_returns_valid_receipt_and_frees_once():
    timings = MessageDebugTimings(
        Queue=1,
        Marshal=2,
        GetParticipants=3,
        GetDevices=4,
        GroupEncrypt=5,
        PeerEncrypt=6,
        Send=7,
        Resp=8,
        Retry=9,
    )
    payload = SendMessageReturnFunction(
        SendResponse=SendResponse(Timestamp=10, ID="receipt", ServerID=11, DebugTimings=timings)
    ).SerializeToString()
    pointer = MagicMock()
    pointer.contents.get_bytes.return_value = payload
    message = Message(conversation="hello")

    with patch("neonize.newsletter_media.free_bytes") as free:
        response = parse_send_response(pointer, message)

    assert response.ID == "receipt"
    assert response.Message == message
    free.assert_called_once_with(pointer)


def test_async_send_message_uses_additive_media_handle_export():
    client = object.__new__(NewAClient)
    client.uuid = b"client"
    native = MagicMock()
    native.SendMessage = AsyncMock()
    native.SendMessageWithMediaHandle = AsyncMock(return_value=MagicMock())
    client._NewAClient__client = native
    to = JID(User="100", Server="newsletter", RawAgent=0, Device=0, Integrator=0)
    message = Message(conversation="hello")

    with patch("neonize.aioze.client.parse_send_response", return_value="sent"):
        result = asyncio.run(client.send_message(to, message, media_handle="handle"))

    assert result == "sent"
    native.SendMessage.assert_not_awaited()
    assert native.SendMessageWithMediaHandle.await_args.args[-1] == b"handle"
