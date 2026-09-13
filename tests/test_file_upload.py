from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from neonize._binder import bind_optional_file_uploads
from neonize.client import NewClient
from neonize.exc import UploadError
from neonize.proto.Neonize_pb2 import JID, UploadResponse, UploadReturnFunction
from neonize.utils.enum import MediaType


def _native_upload_pointer():
    payload = UploadReturnFunction(
        UploadResponse=UploadResponse(
            url="https://media.invalid/file",
            DirectPath="/media/file",
            Handle="upload-handle",
            FileLength=123,
        )
    ).SerializeToString()
    pointer = MagicMock()
    pointer.contents.get_bytes.return_value = payload
    return pointer


def test_optional_file_exports_do_not_break_legacy_binary():
    assert bind_optional_file_uploads(SimpleNamespace()) is False


def test_optional_file_exports_bind_new_binary():
    native = SimpleNamespace(
        UploadFile=MagicMock(),
        UploadNewsletterFile=MagicMock(),
    )

    assert bind_optional_file_uploads(native) is True
    assert native.UploadFile.argtypes
    assert native.UploadNewsletterFile.argtypes


def test_upload_file_passes_only_path_to_native(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"payload")
    client = object.__new__(NewClient)
    client.uuid = b"client"
    native = SimpleNamespace(UploadFile=MagicMock(return_value=_native_upload_pointer()))
    setattr(client, "_NewClient__client", native)

    with patch("neonize.client.free_bytes") as free:
        result = client.upload_file(str(path), MediaType.MediaVideo)

    assert result.FileLength == 123
    native.UploadFile.assert_called_once_with(b"client", os.fsencode(path), MediaType.MediaVideo.value)
    free.assert_called_once()


def test_upload_file_fails_at_capability_boundary(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"payload")
    client = object.__new__(NewClient)
    client.uuid = b"client"
    setattr(client, "_NewClient__client", SimpleNamespace())

    with pytest.raises(UploadError, match="capability is unavailable"):
        client.upload_file(str(path), MediaType.MediaVideo)


def test_send_video_file_preserves_thumbnail_transport_metadata(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"payload")
    upload = SimpleNamespace(
        url="https://media.invalid/file",
        DirectPath="/media/file",
        FileEncSHA256=b"encrypted-hash",
        FileLength=123,
        FileSHA256=b"plain-hash",
        MediaKey=b"media-key",
    )
    client = object.__new__(NewClient)
    client.upload_file = MagicMock(return_value=upload)
    client.send_message = MagicMock(return_value="sent")
    client._parse_mention = MagicMock(return_value=[])
    client._parse_group_mention = MagicMock(return_value=[])
    ffmpeg = MagicMock()
    ffmpeg.extract_info.return_value.format.duration = 12
    ffmpeg.extract_thumbnail.return_value = b"thumbnail"

    with (
        patch("neonize.client.FFmpeg") as ffmpeg_class,
        patch("neonize.client.magic.from_file", return_value="video/mp4"),
    ):
        ffmpeg_class.return_value.__enter__.return_value = ffmpeg
        assert (
            client.send_video_file(
                JID(User="100", Server="s.whatsapp.net"),
                str(path),
                caption="caption",
            )
            == "sent"
        )

    message = client.send_message.call_args.args[1].videoMessage
    assert message.JPEGThumbnail == b"thumbnail"
    assert message.thumbnailDirectPath == upload.DirectPath
    assert message.thumbnailEncSHA256 == upload.FileEncSHA256
    assert message.thumbnailSHA256 == upload.FileSHA256
