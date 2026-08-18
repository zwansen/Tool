import os
import struct
import logging
from pathlib import Path

# 日志文件固定写到「工程根/output/ublox/」——用绝对路径（基于本文件位置），
# 不受启动目录影响（否则从桌面双击启动时会在桌面生成 ubx_parser.log）
_UBX_LOG_DIR = Path(__file__).resolve().parent.parent / "output" / "ublox"
_UBX_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=str(_UBX_LOG_DIR / 'ubx_parser.log')
)

def verify_checksum(data):
    if not isinstance(data, bytes):
        raise TypeError("Input data must be bytes type")
        
    if len(data) < 6:  # Minimum UBX message length (header + length + checksum)
        return False
        
    try:
        ck_a, ck_b = 0, 0
        for byte in data[2:-2]:  # Exclude sync chars and checksum bytes
            ck_a = (ck_a + byte) & 0xFF
            ck_b = (ck_b + ck_a) & 0xFF
        return ck_a == data[-2] and ck_b == data[-1]
    except Exception as e:
        logging.error(f"Checksum verification failed: {str(e)}")
        return False


def parse_ubx_nav_dop(data):
    try:
        if len(data) < 18:  # UBX-NAV-DOP message length is 18 bytes
            raise ValueError("Message too short for UBX-NAV-DOP")
            
        if not verify_checksum(data):
            logging.warning("Invalid checksum in UBX-NAV-DOP message")
            return None
            
        fields = struct.unpack_from('<IHHHHHHH', data, 6)
        return {
            'iTOW': fields[0],
            'gDOP': fields[1]/100.0,
            'pDOP': fields[2]/100.0,
            'tDOP': fields[3]/100.0,
            'vDOP': fields[4]/100.0,
            'hDOP': fields[5]/100.0,
            'nDOP': fields[6]/100.0,
            'eDOP': fields[7]/100.0
        }
    except Exception as e:
        logging.error(f"Failed to parse UBX-NAV-DOP: {str(e)}")
        return None


def parse_ubx_nav_posllh(data):

    try:
        if len(data) < 28:  # UBX-NAV-POSLLH message length is 28 bytes
            raise ValueError("Message too short for UBX-NAV-POSLLH")
            
        if not verify_checksum(data):
            logging.warning("Invalid checksum in UBX-NAV-POSLLH message")
            return None
            
        fields = struct.unpack_from('<IiiiiII', data, 6)
        return {
            'iTOW': fields[0],
            'lon': fields[1]/1e7,
            'lat': fields[2]/1e7,
            'height': fields[3]/1000.0,
            'hMSL': fields[4]/1000.0,
            'hAcc': fields[5]/1000.0,
            'vAcc': fields[6]/1000.0
        }
    except Exception as e:
        logging.error(f"Failed to parse UBX-NAV-POSLLH: {str(e)}")
        return None


def parse_ubx_nav_velned(data):

    try:
        if len(data) < 36:  # UBX-NAV-VELNED message length is 36 bytes
            raise ValueError("Message too short for UBX-NAV-VELNED")
            
        if not verify_checksum(data):
            logging.warning("Invalid checksum in UBX-NAV-VELNED message")
            return None
            
        fields = struct.unpack_from('<IiiiiIIII', data, 6)
        return {
            'iTOW': fields[0],
            'velN': fields[1]/100.0,
            'velE': fields[2]/100.0,
            'velD': fields[3]/100.0,
            'speed': fields[4]/100.0,
            'gSpeed': fields[5]/100.0,
            'heading': fields[6]/1e5,
            'sAcc': fields[7]/100.0,
            'cAcc': fields[8]/1e5
        }
    except Exception as e:
        logging.error(f"Failed to parse UBX-NAV-VELNED: {str(e)}")
        return None


def parse_ubx_nav_status(data):

    try:
        if len(data) < 16:  # UBX-NAV-STATUS message length is 16 bytes
            raise ValueError("Message too short for UBX-NAV-STATUS")
            
        if not verify_checksum(data):
            logging.warning("Invalid checksum in UBX-NAV-STATUS message")
            return None
            
        fields = struct.unpack_from('<IBBBBBBB', data, 6)
        return {
            'iTOW': fields[0],
            'gpsFix': fields[1],
            'flags': fields[2],
            'fixStat': fields[3],
            'flags2': fields[4],
            'ttff': fields[5],
            'msss': fields[6]
        }
    except Exception as e:
        logging.error(f"Failed to parse UBX-NAV-STATUS: {str(e)}")
        return None


def parse_ubx_nav_pvt(data):

    try:
        if len(data) < 100:  # UBX-NAV-PVT message length is 92 bytes
            raise ValueError("Message too short for UBX-NAV-PVT")

        if not verify_checksum(data):
            logging.warning("Invalid checksum in UBX-NAV-PVT message")
            return None

        # Corrected struct format string based on UBX-NAV-PVT specification
        fields = struct.unpack_from('<I H 5B B I i 4B 4i 2I 4i i 2I H 6x i h H', data, 6)
        return {
            'iTOW': fields[0],
            'year': fields[1],
            'month': fields[2],
            'day': fields[3],
            'hour': fields[4],
            'min': fields[5],
            'sec': fields[6],
            'valid': fields[7],
            'tAcc': fields[8],
            'nano': fields[9],
            'fixType': fields[10],
            'flags': fields[11],
            'flags2': fields[12],
            'numSV': fields[13],
            'lon': fields[14]/1e7,
            'lat': fields[15]/1e7,
            'height': fields[16]/1000.0,
            'hMSL': fields[17]/1000.0,
            'hAcc': fields[18]/1000.0,
            'vAcc': fields[19]/1000.0,
            'velN': fields[20]/100.0,
            'velE': fields[21]/100.0,
            'velD': fields[22]/100.0,
            'gSpeed': fields[23]/100.0,
            'heading': fields[24]/1e5,
            'sAcc': fields[25]/100.0,
            'cAcc': fields[26]/1e5
        }
    except Exception as e:
        logging.error(f"Failed to parse UBX-NAV-PVT: {str(e)}", exc_info=True)
        return None


def parse_ubx_nav_sol(data):

        try:
            if len(data) < 52:  # UBX-NAV-SOL message length is 52 bytes
                raise ValueError("Message too short for UBX-NAV-SOL")

            if not verify_checksum(data):
                logging.warning("Invalid checksum in UBX-NAV-SOL message")
                return None

            fields = struct.unpack_from('<I i h B B 3i I 3i I H B B 4x', data, 6)
            return {
                'iTOW': fields[0],
                'fTOW': fields[1],
                'week': fields[2],
                'gpsFix': fields[3],
                'flags': fields[4],
                'ecefX': fields[5],
                'ecefY': fields[6],
                'ecefZ': fields[7],
                'pAcc': fields[8],
                'ecefVX': fields[9],
                'ecefVY': fields[10],
                'ecefVZ': fields[11],
                'sAcc': fields[12],
                'pDOP': fields[13]/100.0,
                'reserved1': fields[14],
                'numSV': fields[15]
            }
        except Exception as e:
            logging.error(f"Failed to parse UBX-NAV-SOL: {str(e)}", exc_info=True)
            return None


def process_ubx_file(file_path):
    results = []
    try:
        with open(file_path, 'rb') as f:
            buffer = bytes()
            while True:
                data = f.read(1024)
                if not data:
                    break
                buffer += data

                while len(buffer) >= 8:
                    # 查找同步字符
                    sync_pos = buffer.find(b'\xb5\x62')
                    if sync_pos == -1:
                        # 保留最后7字节，防止同步字符被分割
                        buffer = buffer[-7:] if len(buffer) >= 7 else bytes()
                        break

                    # 移除同步字符前的数据
                    if sync_pos > 0:
                        logging.debug(f"Discarding {sync_pos} bytes before sync chars")
                        buffer = buffer[sync_pos:]

                    # 检查是否有足够的数据读取头部
                    if len(buffer) < 8:
                        break

                    # 提取消息头
                    msg_class = buffer[2]
                    msg_id = buffer[3]
                    length = int.from_bytes(buffer[4:6], 'little')

                    # 检查完整消息是否可用
                    total_length = 8 + length  # 同步头(2) + 头部(6) + 载荷(length) + 校验和(2)
                    if len(buffer) < total_length:
                        break  # 等待更多数据

                    # 提取完整消息
                    message = buffer[:total_length]
                    buffer = buffer[total_length:]

                    # 解析消息
                    parsed_msg = None
                    try:
                        if msg_class == 0x01:  # NAV class
                            if msg_id == 0x02:  # NAV-POSLLH
                                parsed_msg = parse_ubx_nav_posllh(message)
                            elif msg_id == 0x03:  # NAV-STATUS
                                parsed_msg = parse_ubx_nav_status(message)
                            elif msg_id == 0x12:  # NAV-VELNED
                                parsed_msg = parse_ubx_nav_velned(message)
                            elif msg_id == 0x07:  # NAV-PVT
                                parsed_msg = parse_ubx_nav_pvt(message)
                            elif msg_id == 0x04:  # NAV-DOP
                                parsed_msg = parse_ubx_nav_dop(message)
                            elif msg_id == 0x06:  # NAV-SOL
                                parsed_msg = parse_ubx_nav_sol(message)

                        if parsed_msg:
                            parsed_msg['msg_type'] = f"0x{msg_class:02X}-0x{msg_id:02X}"
                            results.append(parsed_msg)
                    except Exception as e:
                        logging.warning(f"Failed to parse message 0x{msg_class:02X}-0x{msg_id:02X}: {str(e)}")
    except Exception as e:
        logging.error(f"Failed to process UBX file: {str(e)}")
    return results


# Message type to protocol name mapping
MSG_TYPE_MAP = {
    "0x01-0x02": "UBX-NAV-POSLLH",
    "0x01-0x07": "UBX-NAV-PVT",
    "0x01-0x12": "UBX-NAV-VELNED",
    "0x01-0x04": "UBX-NAV-DOP",
    "0x01-0x06": "UBX-NAV-SOL"
}

def format_asc_line(parsed_msg):

    msg_type = parsed_msg.get('msg_type', '')
    protocol_name = MSG_TYPE_MAP.get(msg_type, "UNKNOWN")
    values = []
    
    # Format values based on message type
    if msg_type == "0x01-0x02":  # UBX-NAV-POSLLH
        values = [
            parsed_msg.get('iTOW', ''),
            parsed_msg.get('lon', ''),
            parsed_msg.get('lat', ''),
            parsed_msg.get('height', ''),
            parsed_msg.get('hMSL', ''),
            parsed_msg.get('hAcc', ''),
            parsed_msg.get('vAcc', '')
        ]
    elif msg_type == "0x01-0x07":  # UBX-NAV-PVT
        values = [
            parsed_msg.get('iTOW', ''),
            parsed_msg.get('year', ''),
            parsed_msg.get('month', ''),
            parsed_msg.get('day', ''),
            parsed_msg.get('hour', ''),
            parsed_msg.get('min', ''),
            parsed_msg.get('sec', ''),
            parsed_msg.get('valid', ''),
            parsed_msg.get('tAcc', ''),
            parsed_msg.get('nano', ''),
            parsed_msg.get('fixType', ''),
            parsed_msg.get('flags', ''),
            parsed_msg.get('flags2', ''),
            parsed_msg.get('numSV', ''),
            parsed_msg.get('lon', ''),
            parsed_msg.get('lat', ''),
            parsed_msg.get('height', ''),
            parsed_msg.get('hMSL', ''),
            parsed_msg.get('hAcc', ''),
            parsed_msg.get('vAcc', ''),
            parsed_msg.get('velN', ''),
            parsed_msg.get('velE', ''),
            parsed_msg.get('velD', ''),
            parsed_msg.get('gSpeed', ''),
            parsed_msg.get('heading', ''),
            parsed_msg.get('sAcc', ''),
            parsed_msg.get('cAcc', ''),
        ]
    elif msg_type == "0x01-0x12":  # UBX-NAV-VELNED
        values = [
            parsed_msg.get('iTOW', ''),
            parsed_msg.get('velN', ''),
            parsed_msg.get('velE', ''),
            parsed_msg.get('velD', ''),
            parsed_msg.get('speed', ''),
            parsed_msg.get('gSpeed', ''),
            parsed_msg.get('heading', ''),
            parsed_msg.get('sAcc', ''),
            parsed_msg.get('cAcc', '')
        ]
    elif msg_type == "0x01-0x04":  # UBX-NAV-DOP
        values = [
            parsed_msg.get('iTOW', ''),
            parsed_msg.get('gDOP', ''),
            parsed_msg.get('pDOP', ''),
            parsed_msg.get('tDOP', ''),
            parsed_msg.get('vDOP', ''),
            parsed_msg.get('hDOP', ''),
            parsed_msg.get('nDOP', ''),
            parsed_msg.get('eDOP', '')
        ]
    elif msg_type == "0x01-0x06":  # UBX-NAV-SOL
        values = [
            parsed_msg.get('iTOW', ''),
            parsed_msg.get('fTOW', ''),
            parsed_msg.get('week', ''),
            parsed_msg.get('gpsFix', ''),
            parsed_msg.get('flags', ''),
            parsed_msg.get('ecefX', ''),
            parsed_msg.get('ecefY', ''),
            parsed_msg.get('ecefZ', ''),
            parsed_msg.get('pAcc', ''),
            parsed_msg.get('ecefVX', ''),
            parsed_msg.get('ecefVY', ''),
            parsed_msg.get('ecefVZ', ''),
            parsed_msg.get('sAcc', ''),
            parsed_msg.get('pDOP', ''),
            parsed_msg.get('reserved1', ''),
            parsed_msg.get('numSV', '')
        ]
    else:
        logging.warning(f"Unknown message type: {msg_type}")
        return None
    
    return f"{protocol_name}," + ",".join(map(str, values)) + "\n"


def save_results_to_asc(results, input_path):
    if not results:
        logging.warning("No valid messages to save")
        return
        
    try:
        # Generate absolute output path
        input_dir = os.path.dirname(os.path.abspath(input_path))
        input_name = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(input_dir, f"{input_name}.asc")
        
        # Ensure output directory exists
        os.makedirs(input_dir, exist_ok=True)
        
        # Write results with verification
        line_count = 0
        batch_size = 100  # Write in batches to improve performance
        batch = []
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for msg in results:
                line = format_asc_line(msg)
                if line:  # Skip None or empty lines
                    batch.append(line)
                    line_count += 1
                    
                    # Write batch when full
                    if len(batch) >= batch_size:
                        f.writelines(batch)
                        batch = []
            
            # Write remaining lines in batch
            if batch:
                f.writelines(batch)
                
        logging.info(f"Successfully saved {line_count} messages to {output_path}")
        return True
    except IOError as e:
        logging.error(f"Failed to save results: {str(e)}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"Unexpected error while saving results: {str(e)}", exc_info=True)
        return False
        logging.info(f"Successfully saved {line_count} messages to {output_path}")
        return True
    except IOError as e:
        logging.error(f"Failed to save results: {str(e)}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"Unexpected error while saving results: {str(e)}", exc_info=True)
        return False


def main():
    parser = argparse.ArgumentParser(description='UBX Binary File Parser')
    parser.add_argument('input_file', help='Path to input UBX binary file')
    parser.add_argument('-v', '--verbose', help='Increase output verbosity', action='store_true')

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        logging.info(f"Processing UBX file: {args.input_file}")
        results = process_ubx_file(args.input_file)
        if not results:
            logging.warning("No valid messages found in input file")
            return 1

        if not save_results_to_asc(results, args.input_file):
            return 2

        logging.info("File processing completed successfully")
        return 0
    except Exception as e:
        logging.error(f"Fatal error processing file: {str(e)}", exc_info=True)
        return 3

    logging.info("File processing completed successfully")
    return 0


if __name__ == "__main__":
    import argparse
    import json
    import sys

    # Configure logging（写 output/ublox/，不污染工程根目录）
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        filename=str(_UBX_LOG_DIR / 'ubx_parser.log')
    )

    main()