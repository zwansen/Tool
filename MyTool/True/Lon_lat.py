import pandas as pd
import re
import os
from typing import List, Optional, Tuple


def dms_to_dd(degrees: float, minutes: float, seconds: float) -> float:
    """
    度分秒转十进制度
    """
    return degrees + minutes / 60.0 + seconds / 3600.0


def dms_to_dm(degrees: float, minutes: float, seconds: float) -> Tuple[float, float]:
    """
    度分秒转度分（度 + 分.秒/60）
    """
    total_minutes = minutes + seconds / 60.0
    return degrees, total_minutes


def parse_dms_string(dms_str: str) -> Tuple[float, float, float]:
    """
    解析度分秒字符串（如 "23 09 26.39095"）
    """
    parts = dms_str.strip().split()
    if len(parts) != 3:
        raise ValueError(f"无效的度分秒格式: {dms_str}")

    degrees = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2])

    return degrees, minutes, seconds


def format_dm_continuous(degrees: float, minutes: float) -> str:
    """
    格式化度分输出（连续格式，无空格）
    例如：23° 9.439849' → 2309.439849
    """
    # 将度数和分钟连在一起，格式化为字符串
    return f"{degrees:.0f}{minutes:09.6f}"


def format_dd(decimal_degrees: float) -> str:
    """
    格式化十进制度输出
    """
    return f"{decimal_degrees:.6f}"


def convert_inertial_explorer_file(input_file: str, output_file: str, conversion_type: str):
    """
    转换Inertial Explorer文件为逗号分隔格式，并转换经纬度
    """
    # 检查文件是否存在
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"输入文件不存在: {input_file}")

    # 读取文件内容
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(input_file, 'r', encoding='gbk') as f:
            lines = f.readlines()

    # 找到数据表开始的行（包含 'GPSTime' 的行）
    data_start_line = -1
    for i, line in enumerate(lines):
        if line.startswith('  GPSTime'):
            data_start_line = i
            break

    if data_start_line == -1:
        raise ValueError("未找到包含'GPSTime'的表头行")

    # 定义正确的表头顺序（根据原始数据列索引）
    fixed_headers = [
        'GPSTime',
        'Latitude_DM' if conversion_type == 'dm' else 'Latitude_DD',
        'Longitude_DM' if conversion_type == 'dm' else 'Longitude_DD',
        'H-MSL',      # 索引7
        'Heading',    # 索引8
        'Pitch',      # 索引9
        'Roll',       # 索引10
        'Q',          # 索引11
        'VNorth',     # 索引12
        'VEast',      # 索引13
        'VUp',        # 索引14
        'H-Ell',      # 索引15
        'AccBiasX',   # 索引16
        'GyroDriftX', # 索引17
        'AccBiasY',   # 索引18
        'GyroDriftY', # 索引19
        'AccBiasZ',   # 索引20
        'GyroDriftZ'  # 索引21
        # 注意：最后一个H-MSL(索引22)是重复的，不包含
    ]

    # 处理数据行
    converted_data = []
    error_count = 0
    success_count = 0

    print(f"开始处理数据行，共 {len(lines) - data_start_line - 1} 行需要处理...")
    print(f"转换类型: {'度分格式' if conversion_type == 'dm' else '十进制度格式'}")

    for line_num, line in enumerate(lines[data_start_line + 1:], data_start_line + 2):
        line = line.strip()
        if not line:
            continue

        # 按空格分割原始数据
        row_data = re.split(r'\s+', line)
        row_data = [d.strip() for d in row_data if d.strip()]

        # 检查数据行是否完整（至少需要前22列）
        if len(row_data) < 22:
            if error_count < 5:  # 只显示前5个错误
                print(f"警告: 第{line_num}行数据不完整，跳过（列数: {len(row_data)}）")
            error_count += 1
            continue

        try:
            # 解析经纬度（纬度：第1-3列，经度：第4-6列）
            lat_deg, lat_min, lat_sec = parse_dms_string(f"{row_data[1]} {row_data[2]} {row_data[3]}")
            lon_deg, lon_min, lon_sec = parse_dms_string(f"{row_data[4]} {row_data[5]} {row_data[6]}")

            # 根据选择的转换类型进行处理
            if conversion_type == 'dm':  # 度分格式
                # 转换为度分
                lat_d, lat_m = dms_to_dm(lat_deg, lat_min, lat_sec)
                lon_d, lon_m = dms_to_dm(lon_deg, lon_min, lon_sec)

                # 格式化输出（连续格式）
                lat_str = format_dm_continuous(lat_d, lat_m)
                lon_str = format_dm_continuous(lon_d, lon_m)

            else:  # 十进制度格式
                # 转换为十进制度
                lat_dd = dms_to_dd(lat_deg, lat_min, lat_sec)
                lon_dd = dms_to_dd(lon_deg, lon_min, lon_sec)

                # 格式化输出
                lat_str = format_dd(lat_dd)
                lon_str = format_dd(lon_dd)

            # 创建新的行数据（严格按照fixed_headers的顺序和原始数据索引）
            row_values = []

            # 1. GPSTime（索引0）
            row_values.append(row_data[0])

            # 2. 转换后的纬度（替换索引1-3）
            row_values.append(lat_str)

            # 3. 转换后的经度（替换索引4-6）
            row_values.append(lon_str)

            # 4. H-MSL（索引7）
            row_values.append(row_data[7])

            # 5. Heading（索引8）
            row_values.append(row_data[8])

            # 6. Pitch（索引9）
            row_values.append(row_data[9])

            # 7. Roll（索引10）
            row_values.append(row_data[10])

            # 8. Q（索引11）
            row_values.append(row_data[11])

            # 9. VNorth（索引12）
            row_values.append(row_data[12])

            # 10. VEast（索引13）
            row_values.append(row_data[13])

            # 11. VUp（索引14）
            row_values.append(row_data[14])

            # 12. H-Ell（索引15）
            row_values.append(row_data[15])

            # 13. AccBiasX（索引16）
            row_values.append(row_data[16])

            # 14. GyroDriftX（索引17）
            row_values.append(row_data[17])

            # 15. AccBiasY（索引18）
            row_values.append(row_data[18])

            # 16. GyroDriftY（索引19）
            row_values.append(row_data[19])

            # 17. AccBiasZ（索引20）
            row_values.append(row_data[20])

            # 18. GyroDriftZ（索引21）
            row_values.append(row_data[21])

            # 用逗号连接所有值
            csv_line = ','.join(row_values)
            converted_data.append(csv_line)
            success_count += 1

            # 显示前3行的转换结果
            if success_count <= 3:
                print(f"转换示例 {success_count}:")
                print(f"  原始数据: {row_data[0]} | {row_data[7]} | {row_data[8]} | {row_data[9]} | {row_data[10]} | {row_data[11]} | ...")
                print(f"  转换后: {lat_str} | {lon_str}")
                print(f"  第4列(H-MSL): {row_data[7]}")
                print(f"  第5列(Heading): {row_data[8]}")
                print(f"  第6列(Pitch): {row_data[9]}")
                print(f"  第7列(Roll): {row_data[10]}")
                print(f"  第8列(Q): {row_data[11]}")
                print()

        except (IndexError, ValueError, TypeError) as e:
            if error_count < 5:  # 只显示前5个错误
                print(f"警告: 第{line_num}行转换失败: {e} (列数: {len(row_data)})")
            error_count += 1
            continue

    if not converted_data:
        raise ValueError("没有成功转换任何数据")

    # 写入输出文件（CSV格式）
    with open(output_file, 'w', encoding='utf-8') as f:
        # 写入表头
        header_line = ','.join(fixed_headers)
        f.write(header_line + '\n')

        # 写入数据
        for row in converted_data:
            f.write(row + '\n')

    print(f"\n转换完成!")
    print(f"输入文件: {input_file}")
    print(f"输出文件: {output_file}")
    print(f"转换类型: {'度分格式(DM)' if conversion_type == 'dm' else '十进制度格式(DD)'}")
    print(f"总行数: {len(lines) - data_start_line - 1}")
    print(f"成功转换: {success_count} 行")
    print(f"失败行数: {error_count} 行")
    print(f"成功率: {success_count / (success_count + error_count) * 100:.1f}%")
    print(f"输出格式: 逗号分隔(CSV)")


def main():
    print("=" * 50)
    print("Inertial Explorer 数据转换工具")
    print("=" * 50)
    print("功能: 转换为逗号分隔格式，经纬度格式转换")
    print("转换选项:")
    print("  1. 度分格式 (DM) - 如: 2309.439849")
    print("  2. 十进制度格式 (DD) - 如: 23.157331")

    try:
        # 获取转换类型选择
        while True:
            choice = input("\n请选择转换类型 (输入 1 或 2): ").strip()
            if choice == '1':
                conversion_type = 'dm'
                break
            elif choice == '2':
                conversion_type = 'dd'
                break
            else:
                print("无效选择，请输入 1 或 2")

        # 获取输入文件路径
        input_file = input("请输入输入文件路径（或按回车使用默认值 '100C.txt'）: ").strip()
        if not input_file:
            input_file = "100C.txt"

        # 获取输出文件路径
        output_file = input("请输入输出文件路径（或按回车使用默认值 '100C.csv'）: ").strip()
        if not output_file:
            output_file = "100C.csv"

        # 清理路径
        input_file = os.path.normpath(input_file.strip().strip('"').strip("'"))
        output_file = os.path.normpath(output_file.strip().strip('"').strip("'"))

        # 检查输入文件
        if not os.path.exists(input_file):
            raise FileNotFoundError(f"输入文件不存在: {input_file}")

        # 检查输出目录
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(f"已创建输出目录: {output_dir}")

        print(f"\n开始转换...")
        convert_inertial_explorer_file(input_file, output_file, conversion_type)

        # 显示文件预览
        print(f"\n输出文件预览（前3行）:")
        with open(output_file, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i < 4:  # 表头 + 3行数据
                    print(line.strip())
                else:
                    break

    except Exception as e:
        print(f"处理过程中出现错误: {e}")
        import traceback
        traceback.print_exc()

    print("\n程序结束。")


if __name__ == "__main__":
    main()