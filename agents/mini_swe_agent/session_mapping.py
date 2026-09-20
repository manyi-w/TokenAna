"""Lossless native-message mapping. No mini or model dependencies at import time."""
from copy import deepcopy


def plain(value):
    if hasattr(value, 'model_dump'):
        return plain(value.model_dump(mode='json'))
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return deepcopy(value)


class NativeHistory:
    def __init__(self, mode=None):
        self.identities = {}
        self.sequence = 0
        self.mode = mode
        self.wire_previous = []
        self.protected = set()
        self.started = False

    def encode_wire(self, messages):
        used = set()
        for message in messages:
            for old in self.wire_previous:
                if id(old) not in used and old == message:
                    self.identities[id(message)] = (self.identity(old), message)
                    used.add(id(old))
                    break
        return self.encode(messages)

    def identity(self, message):
        entry = self.identities.get(id(message))
        if entry is None:
            self.sequence += 1
            entry = (f'm{self.sequence:06d}', message)
            self.identities[id(message)] = entry  # Retain reference against Python ID reuse.
        return entry[0]

    def encode(self, messages):
        result = []
        for message in messages:
            identity = self.identity(message)
            native = plain(message)
            field = 'output' if native.get('type') in ('function_call_output', 'custom_tool_call_output') else 'content'
            text = native.get(field)
            if isinstance(text, str):
                native.pop(field)
            else:
                text, field = '', None
            calls = [item['id'] for item in message.get('tool_calls') or []]
            if message.get('type') in ('function_call', 'custom_tool_call'):
                calls.append(message['call_id'])
            if message.get('object') == 'response':
                calls += [item.get('call_id') or item.get('id') for item in message.get('output', [])
                          if item.get('type') == 'function_call']
            tool_result = (message.get('call_id') if message.get('type') in ('function_call_output', 'custom_tool_call_output')
                           else message.get('tool_call_id'))
            # Trae emits a separate Anthropic message for each tool block. Keep
            # signatures, arguments and every other block property immutable.
            content = native.get('content')
            if isinstance(content, list) and len(content) == 1:
                block = content[0]
                if block.get('type') == 'tool_use':
                    calls.append(block['id'])
                elif block.get('type') == 'tool_result':
                    tool_result = block['tool_use_id']
                    if isinstance(block.get('content'), str):
                        text = block.pop('content')
                        field = ['content', 0, 'content']
                elif block.get('type') in ('text', 'input_text', 'output_text'):
                    text = block.pop('text')
                    field = ['content', 0, 'text']
            display_text = None
            if field is None:
                def texts(value):
                    if isinstance(value, list):
                        return [text for item in value for text in texts(item)]
                    if isinstance(value, dict):
                        if value.get('type') in ('text', 'input_text', 'output_text') and isinstance(value.get('text'), str):
                            return [value['text']]
                        return texts(value.get('content', value.get('output', [])))
                    return []
                text = '\n'.join(texts(native))
                display_text = text
            role = ('assistant' if message.get('object') == 'response' or message.get('type') == 'reasoning' else
                    'tool' if tool_result else 'assistant' if calls else message.get('role', 'unknown'))
            if role == 'assistant':
                self.started = True
            if not self.started and role == 'user' and not tool_result:
                self.protected.add(identity)
            result.append({'id': identity, 'role': role, 'text': text,
                'native': {'message': native, 'text_field': field, **({'display_text': display_text} if display_text else {})},
                'editable': role not in ('system', 'developer', 'exit') and
                    identity not in self.protected,
                'tool_calls': calls, 'tool_result': tool_result})
        return result

    def decode(self, encoded):
        messages = []
        for mapped in encoded:
            if mapped['native'] == {'summary': True}:
                native = ({'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': mapped['text']}]}
                          if self.mode == 'responses_items' else {'role': 'assistant', 'content': mapped['text']})
                self.identities[id(native)] = (mapped['id'], native)
                messages.append(native)
                continue
            native = deepcopy(mapped['native']['message'])
            field = mapped['native']['text_field']
            if isinstance(field, list):
                container = native
                for key in field[:-1]:
                    container = container[key]
                container[field[-1]] = mapped['text']
            elif field:
                native[field] = mapped['text']
            elif mapped['text'] and mapped['text'] != mapped['native'].get('display_text'):
                raise ValueError('opaque native blocks cannot be replaced with text')
            self.identities[id(native)] = (mapped['id'], native)
            messages.append(native)
        self.wire_previous = messages
        return messages
