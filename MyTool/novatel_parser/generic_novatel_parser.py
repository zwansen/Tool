#!/usr/bin/env python3
"""
通用 NovAtel 风格二进制消息解析器。
通过 JSON 配置文件定义消息头和各种消息体，无需改代码即可解析新的语句。
"""
import json
import struct
import csv
import os
import sys
import argparse

DEFAULT_TYPE_LEN = {
    'UChar': 1, 'Char': 1,
    'UShort': 2, 'Short': 2,
    'ULong': 4, 'Long': 4,
    'Float': 4, 'Double': 8
}

STRUCT_FMT = {
    'UChar': '<B', 'Char': '<b',
    'UShort': '<H', 'Short': '<h',
    'ULong': '<I', 'Long': '<i',
    'Float': '<f', 'Double': '<d'
}

ENUM_FMT = {1: '<B', 2: '<H', 4: '<I'}


def field_length(field):
    t = field['type']
    if t == 'bytes':
        return field['length']
    if t == 'Enum':
        return field['length']
    return DEFAULT_TYPE_LEN[t]


def parse_field(data, offset, field):
    t = field['type']
    if t == 'bytes':
        length = field['length']
        raw = data[offset:offset + length]
        return raw.hex(' ').upper(), length
    if t == 'Enum':
        length = field['length']
        fmt = ENUM_FMT.get(length, '<I')
        val = struct.unpack_from(fmt, data, offset)[0]
        return val, length
    fmt = STRUCT_FMT[t]
    val = struct.unpack_from(fmt, data, offset)[0]
    return val, field_length(field)


def parse_header(data, offset, config):
    pos = offset
    values = {}
    for field in config['header']:
        name = field['name']
        val, consumed = parse_field(data, pos, field)
        values[name] = val
        pos += consumed
        if name == 'headerLength':
            values['_headerLength'] = val
        if name == 'messageId':
            values['_messageId'] = val
        if name == 'messageLength':
            values['_messageLength'] = val
    return values


def parse_payload(data, offset, msg_len, msg_def):
    pos = offset
    values = {}
    fields = msg_def.get('fields', []) if msg_def else [{'name': 'payloadHex'}]
    for field in fields:
        if field.get('type') == 'CRC':
            continue
        name = field['name']
        if name == 'payloadHex':
            val = data[pos:pos + msg_len].hex(' ').upper()
            consumed = msg_len
        else:
            val, consumed = parse_field(data, pos, field)
        values[name] = val
        pos += consumed
    return values


def parse_one(data, offset, config):
    sync_str = config['sync'].replace(' ', '')
    sync_bytes = bytes.fromhex(sync_str)
    if data[offset:offset + len(sync_bytes)] != sync_bytes:
        return None

    header = parse_header(data, offset, config)
    header_len = header.get('_headerLength', config.get('headerLength', 28))
    msg_id = header.get('_messageId')
    msg_len = header.get('_messageLength', 0)

    payload_start = offset + header_len
    payload_end = payload_start + msg_len
    crc_end = payload_end + 4
    if crc_end > len(data):
        return None

    msg_def = config.get('messages', {}).get(str(msg_id))
    if msg_def:
        msg_name = msg_def.get('name', f'MSG_{msg_id}')
    else:
        msg_name = f'UNKNOWN_{msg_id}'

    body = parse_payload(data, payload_start, msg_len, msg_def)
    crc = struct.unpack_from('<I', data, payload_end)[0]

    return {
        'offset': offset,
        'messageId': msg_id,
        'messageName': msg_name,
        'header': header,
        'body': body,
        'CRC': crc
    }


def parse_all(data, config):
    sync_str = config['sync'].replace(' ', '')
    sync_bytes = bytes.fromhex(sync_str)
    n = len(data)
    records = []
    i = 0
    while i <= n - len(sync_bytes):
        if data[i:i + len(sync_bytes)] != sync_bytes:
            i += 1
            continue
        rec = parse_one(data, i, config)
        if rec:
            header_len = rec['header'].get('_headerLength', config.get('headerLength', 28))
            msg_len = rec['header'].get('_messageLength', 0)
            records.append(rec)
            i = rec['offset'] + header_len + msg_len + 4
        else:
            i += 1
    return records


def _entry_to_name_desc(entry):
    if isinstance(entry, dict):
        name = entry.get('name', '')
        desc = entry.get('desc', '')
        return name, desc
    return entry, entry


def resolve_enum(enum_map, value):
    """根据枚举表解析名称和描述，支持精确值、范围 a-b、以及 _default 默认值。"""
    str_val = str(value)
    if str_val in enum_map:
        return _entry_to_name_desc(enum_map[str_val])

    # 范围匹配，例如 "3-7": "RSV"
    for key in enum_map:
        if '-' in key:
            try:
                a, b = map(int, key.split('-'))
                if a <= value <= b:
                    return _entry_to_name_desc(enum_map[key])
            except ValueError:
                continue

    # 默认值
    if '_default' in enum_map:
        return _entry_to_name_desc(enum_map['_default'])

    return f"UNKNOWN({value})", f"UNKNOWN({value})"


def build_row(rec, config, raw_only):
    header_fields = config['header']
    msg_def = config.get('messages', {}).get(str(rec['messageId']))
    body_fields = msg_def.get('fields', []) if msg_def else [{'name': 'payloadHex'}]
    enums = config.get('enums', {})

    row = {
        'messageName': rec['messageName'],
        'msg_index': 0,
        'file_offset': rec['offset']
    }

    for field in header_fields:
        name = field['name']
        row[name] = rec['header'][name]
        if not raw_only and field.get('enum'):
            enum_map = enums.get(field['enum'], {})
            name_str, desc = resolve_enum(enum_map, row[name])
            if name_str:
                row[f"{name}_name"] = name_str
            if desc:
                row[f"{name}_desc"] = desc

    for field in body_fields:
        if field.get('type') == 'CRC':
            continue
        name = field['name']
        row[name] = rec['body'].get(name)
        if not raw_only and field.get('enum'):
            enum_map = enums.get(field['enum'], {})
            name_str, desc = resolve_enum(enum_map, row[name])
            if name_str:
                row[f"{name}_name"] = name_str
            if desc:
                row[f"{name}_desc"] = desc

    row['CRC'] = rec['CRC']
    return row


def main():
    parser = argparse.ArgumentParser(
        description='通用 NovAtel 风格二进制消息解析器（JSON 配置驱动）')
    parser.add_argument('binary', help='要解析的二进制日志文件')
    parser.add_argument('-c', '--config', default='message_definitions.json',
                        help='字段定义 JSON 文件，默认 message_definitions.json')
    parser.add_argument('-o', '--output', default=None,
                        help='输出目录，默认使用输入文件所在目录')
    parser.add_argument('--raw-only', action='store_true',
                        help='只输出原始数值，不输出 *_name / *_desc 枚举解释列')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)

    with open(args.binary, 'rb') as f:
        data = f.read()

    records = parse_all(data, config)

    out_dir = args.output if args.output else os.path.dirname(os.path.abspath(args.binary))
    os.makedirs(out_dir, exist_ok=True)

    # 按 messageId 分组，每种消息类型单独一个 CSV
    groups = {}
    for rec in records:
        key = (rec['messageId'], rec['messageName'])
        groups.setdefault(key, []).append(rec)

    for (msg_id, msg_name), recs in groups.items():
        rows = []
        for idx, rec in enumerate(recs, 1):
            row = build_row(rec, config, args.raw_only)
            row['msg_index'] = idx
            rows.append(row)

        safe_name = msg_name.replace(' ', '_').replace('/', '_')
        csv_path = os.path.join(out_dir, f'msg_{msg_id}_{safe_name}.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f'{csv_path}: {len(rows)} 行')

    print(f"\n共解析 {len(records)} 条消息，{len(groups)} 种类型。")


if __name__ == '__main__':
    main()
