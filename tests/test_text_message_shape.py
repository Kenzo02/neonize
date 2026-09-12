"""Plain strings must use conversation; explicit previews and mentions stay rich."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neonize.client import NewClient
from neonize.aioze.client import NewAClient
from neonize.utils.jid import build_jid
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ExtendedTextMessage, Message


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('case', ['plain', 'preview_miss', 'preview_none', 'mention', 'protobuf'])
def test_send_text_preserves_presence_and_rich_context(asynchronous, case):
    cls = NewAClient if asynchronous else NewClient
    client = object.__new__(cls)
    client.uuid = b'local'
    native = MagicMock()
    native.SendMessage = AsyncMock() if asynchronous else MagicMock()
    setattr(client, f'_{cls.__name__}__client', native)
    client._parse_group_mention = AsyncMock(return_value=[]) if asynchronous else MagicMock(return_value=[])
    client._parse_mention = MagicMock(return_value=['100@s.whatsapp.net'] if case == 'mention' else [])
    preview = ExtendedTextMessage(previewType=ExtendedTextMessage.NONE, title='Title') if case == 'preview_none' else None
    client._generate_link_preview = AsyncMock(return_value=preview) if asynchronous else MagicMock(return_value=preview)
    content = Message(conversation='4') if case == 'protobuf' else '4'
    module = 'neonize.aioze.client' if asynchronous else 'neonize.client'
    with patch(f'{module}.parse_send_response', side_effect=lambda pointer, message: message):
        result = client.send_message(build_jid('123', 'newsletter'), content, link_preview=case.startswith('preview'))
        if asynchronous:
            result = asyncio.run(result)
    call = native.SendMessage.await_args if asynchronous else native.SendMessage.call_args
    wire = Message.FromString(call.args[3])
    assert wire == result
    if case in ('plain', 'preview_miss', 'protobuf'):
        assert wire.HasField('conversation')
        assert wire.conversation == '4'
        assert not wire.HasField('extendedTextMessage')
    elif case == 'preview_none':
        assert wire.extendedTextMessage.HasField('previewType')
        assert wire.extendedTextMessage.title == 'Title'
    else:
        assert list(wire.extendedTextMessage.contextInfo.mentionedJID) == ['100@s.whatsapp.net']
