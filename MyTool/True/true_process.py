import pandas as pd
from datetime import datetime, timedelta
import re
import os
from typing import List, Optional, Tuple


def read_inertial_explorer_file(file_path: str) -> pd.DataFrame:
    """
    读取 Inertial Explorer 输出的文本文件
    """
    # 检查文件是否存在
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 读取文件内容
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        # 尝试其他编码
        with open(file_path, 'r', encoding='gbk') as f:
            lines = f.readlines()

    # 找到数据表开始的行（包含 'GPSTime' 的行）
    data_start_line = -1
    for i, line in enumerate(lines):
        if line.startswith('  GPSTime'):
            data_start_line = i
            break

    if data_start_line == -1:
        raise ValueError("未找到包含'GPSTime'的表头行")

    # 提取表头（根据实际数据列顺序调整）
    header = [
        'GPSTime',
        'Lat_deg', 'Lat_min', 'Lat_sec',  # 纬度度、分、秒
        'Lon_deg', 'Lon_min', 'Lon_sec',  # 经度度、分、秒
        'H-MSL',  # 海拔高 - 第7列
        'Heading', 'Pitch', 'Roll',
        'Q',
        'VNorth', 'VEast', 'VUp',
        'H-Ell',  # 椭球高 - 第15列 ← 这是我们要使用的高程
        'AccBiasX', 'GyroDriftX',
        'AccBiasY', 'GyroDriftY',
        'AccBiasZ', 'GyroDriftZ',
        'Week'  # 可能不存在或无效
    ]

    print(f"使用固定表头，共 {len(header)} 列")

    # 处理数据行
    data = []
    error_lines = []

    for line_num, line in enumerate(lines[data_start_line + 1:], data_start_line + 2):
        line = line.strip()
        if not line:
            continue

        # 按空格分割数据
        row_data = re.split(r'\s+', line)
        row_data = [d.strip() for d in row_data if d.strip()]

        # 检查数据行是否完整
        if len(row_data) < 23:
            error_lines.append((line_num, f"列数不足: {len(row_data)}"))
            continue

        # 取前23列（忽略可能的多余列）
        row_data = row_data[:23]
        data.append(row_data)

        # 显示前3行的解析结果
        if len(data) <= 3:
            print(f"第{len(data)}行数据预览:")
            print(f"  GPSTime: {row_data[0]}")
            print(f"  纬度: {row_data[1]}° {row_data[2]}' {row_data[3]}\"")
            print(f"  经度: {row_data[4]}° {row_data[5]}' {row_data[6]}\"")
            print(f"  H-MSL(海拔高): {row_data[7]}")
            print(f"  H-Ell(椭球高): {row_data[15]} ← 将用于GGA高程")

    if not data:
        raise ValueError("没有读取到有效数据")

    # 输出错误行信息
    if error_lines:
        print(f"\n警告: 跳过 {len(error_lines)} 行不完整数据:")
        for line_num, error_msg in error_lines[:5]:
            print(f"  第{line_num}行: {error_msg}")
        if len(error_lines) > 5:
            print(f"  ... 还有 {len(error_lines) - 5} 个错误未显示")

    print(f"成功读取 {len(data)} 行有效数据")

    # 创建DataFrame
    df = pd.DataFrame(data, columns=header)

    # 转换数据类型
    numeric_columns = ['GPSTime', 'Lat_deg', 'Lat_min', 'Lat_sec', 'Lon_deg', 'Lon_min', 'Lon_sec',
                       'H-MSL', 'H-Ell', 'Week']

    for col in numeric_columns:
        try:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        except Exception as e:
            print(f"警告: 列 {col} 转换失败: {e}")

    # 转换Q列为整数
    try:
        df['Q'] = pd.to_numeric(df['Q'], errors='coerce').fillna(0).astype(int)
    except Exception as e:
        print(f"警告: Q列转换失败: {e}")
        df['Q'] = 0

    return df


def gps_to_utc_time(gps_week: float, gps_seconds: float, leap_seconds: int = 18) -> datetime:
    """
    GPS时间转UTC时间（当有有效周数时使用）
    GPS时间 = UTC时间 + 闰秒
    """
    gps_epoch = datetime(1980, 1, 6, 0, 0, 0)
    total_seconds = gps_week * 604800 + gps_seconds - leap_seconds
    utc_time = gps_epoch + timedelta(seconds=total_seconds)
    return utc_time


def seconds_to_utc_time_string(gps_seconds: float, leap_seconds: int = 18) -> str:
    """
    将GPS周内秒转换为UTC时间格式 (hhmmss.ss)
    GPS时间 = UTC时间 + 闰秒
    所以：UTC时间 = GPS时间 - 闰秒
    """
    # 减去闰秒得到UTC时间
    utc_seconds = gps_seconds - leap_seconds

    # 处理负值（如果周内秒小于闰秒数，说明跨越了日边界）
    if utc_seconds < 0:
        utc_seconds += 86400  # 加一天

    # 转换为一天内的时间格式
    seconds_in_day = utc_seconds % 86400

    hours = int(seconds_in_day // 3600)
    minutes = int((seconds_in_day % 3600) // 60)
    seconds_remainder = seconds_in_day % 60

    return f"{hours:02d}{minutes:02d}{seconds_remainder:06.3f}"


def dms_to_dm(degrees: float, minutes: float, seconds: float, is_longitude: bool = False, decimal_places: int = 8) -> \
Optional[str]:
    """
    度分秒转度分格式 - 正确保留8位小数
    """
    try:
        # 验证输入值范围
        if is_longitude:
            if abs(degrees) > 180 or minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
                return None
        else:
            if abs(degrees) > 90 or minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
                return None

        total_minutes = minutes + seconds / 60.0

        # 分离整数部分和小数部分
        minutes_int = int(total_minutes)  # 分的整数部分
        minutes_frac = total_minutes - minutes_int  # 分的小数部分

        # 格式化小数部分（8位）
        frac_str = f"{minutes_frac:.{decimal_places}f}"[2:]  # 去掉"0."

        if is_longitude:
            # 经度：DDDMM.mmmmmmmm
            return f"{int(degrees):03d}{minutes_int:02d}.{frac_str}"
        else:
            # 纬度：DDMM.mmmmmmmm
            return f"{int(degrees):02d}{minutes_int:02d}.{frac_str}"

    except (ValueError, TypeError) as e:
        print(f"经纬度转换错误: {degrees} {minutes} {seconds}, 错误: {e}")
        return None


def generate_gga(time_str: str, lat_dm: str, lon_dm: str, quality: int,
                 altitude: float, satellites: str = '12', hdop: str = '1.0') -> Optional[str]:
    """
    生成GGA语句 - 使用椭球高(H-Ell)
    标准格式: $GPGGA,hhmmss.ss,llll.llll,a,yyyyy.yyyyy,a,x,xx,x.x,x.x,M,x.x,M,x.x,xxxx*hh
    """
    try:
        # 确定半球
        try:
            lat_hemisphere = 'N' if float(lat_dm[:2]) >= 0 else 'S'
            lon_hemisphere = 'E' if float(lon_dm[:3]) >= 0 else 'W'
        except ValueError:
            return None

        # 拼接GGA字段（修正定位质量字段位置）
        gga_fields = [
            f"GNGGA,{time_str}",           # 1. UTC时间
            f"{lat_dm},{lat_hemisphere}",  # 2. 纬度 + 半球
            f"{lon_dm},{lon_hemisphere}",  # 3. 经度 + 半球
            f"{quality}",                  # 4. 定位质量 ← 修正位置！
            f"{satellites}",               # 5. 使用卫星数量
            f"{hdop}",                     # 6. 水平精度因子
            f"{altitude:.3f},M",           # 7. 海拔高度 + 单位
            ",,",                          # 8-9. 大地水准面起伏和年龄字段为空
            ",,",                          # 10-11. 差分数据年龄和站ID为空
        ]

        gga_str = ','.join(gga_fields)

        # 计算校验和
        checksum = 0
        for char in gga_str:
            checksum ^= ord(char)

        return f"${gga_str}*{checksum:02X}"
    except Exception as e:
        print(f"GGA语句生成错误: {e}")
        return None


def clean_file_path(path: str) -> str:
    """
    清理文件路径，去除引号并标准化路径
    """
    # 去除首尾的引号和空格
    path = path.strip().strip('"').strip("'")

    # 标准化路径（将斜杠统一）
    path = os.path.normpath(path)

    return path


def get_file_path(prompt: str, default_path: str = "") -> str:
    """
    获取用户输入的文件路径，支持默认值和路径验证
    """
    while True:
        path = input(prompt).strip()

        # 如果用户输入为空，使用默认值
        if not path and default_path:
            path = default_path

        if path:
            # 清理路径
            path = clean_file_path(path)
            return path
        else:
            print("路径不能为空，请重新输入。")


def main():
    print("=" * 50)
    print("Inertial Explorer 转 GGA 语句转换器")
    print("=" * 50)
    print("注意: GGA语句使用UTC时间（GPS时间减去18秒闰秒）")
    print("      使用椭球高(H-Ell)作为GGA高程值")

    try:
        # 获取输入文件路径
        input_file = get_file_path(
            "请输入输入文件路径（或按回车使用默认值 '100C.txt'）: ",
            "100C.txt"
        )

        # 获取输出文件路径
        output_file = get_file_path(
            "请输入输出文件路径（或按回车使用默认值 '100C_gga.txt'）: ",
            "100C_gga.txt"
        )

        print(f"\n处理路径信息:")
        print(f"输入文件: {input_file}")
        print(f"输出文件: {output_file}")

        # 检查输入文件是否存在
        if not os.path.exists(input_file):
            raise FileNotFoundError(f"输入文件不存在: {input_file}")

        # 检查输出目录是否存在
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(f"已创建输出目录: {output_dir}")

        print(f"\n开始处理文件...")

        # 读取数据
        df = read_inertial_explorer_file(input_file)

        # 分析时间序列
        print(f"\n时间序列分析:")
        print(f"GPSTime范围: {df['GPSTime'].min():.2f} - {df['GPSTime'].max():.2f}")
        print(f"数据点数: {len(df)}")

        # 计算时间间隔
        time_diffs = df['GPSTime'].diff().dropna()
        if len(time_diffs) > 0:
            print(f"平均时间间隔: {time_diffs.mean():.3f} 秒")
            print(f"最大时间间隔: {time_diffs.max():.3f} 秒")
            print(f"最小时间间隔: {time_diffs.min():.3f} 秒")

        # 检查Week列是否有效
        week_valid = False
        if 'Week' in df.columns and df['Week'].notna().any():
            week_value = df['Week'].iloc[0]
            # 检查周数是否在合理范围内（通常GPS周数大于1000）
            if week_value > 1000 and week_value < 3000:
                week_valid = True
                print(f"使用GPS周数: {week_value} (绝对UTC时间模式)")
            else:
                print(f"周数 {week_value} 无效，使用相对UTC时间模式")
        else:
            print("Week列不存在或全为空，使用相对UTC时间模式")

        # 高程统计信息
        print(f"\n高程统计信息:")
        print(f"  H-MSL(海拔高)范围: {df['H-MSL'].min():.3f} - {df['H-MSL'].max():.3f} 米")
        print(f"  H-Ell(椭球高)范围: {df['H-Ell'].min():.3f} - {df['H-Ell'].max():.3f} 米")
        print(f"  平均差值(H-MSL - H-Ell): {(df['H-MSL'] - df['H-Ell']).mean():.3f} 米")

        # 生成GGA语句
        gga_sentences = []
        success_count = 0
        error_count = 0

        print(f"\n开始生成GGA语句...")
        print(f"闰秒修正: -18秒 (GPS时间 → UTC时间)")

        for index, row in df.iterrows():
            try:
                # 时间处理策略 - 确保使用UTC时间
                if week_valid:
                    # 模式1：有有效周数，使用绝对UTC时间
                    try:
                        utc_time = gps_to_utc_time(row['Week'], row['GPSTime'])
                        time_str = utc_time.strftime('%H%M%S.%f')[:-4]
                        time_mode = "绝对UTC时间"
                    except Exception as e:
                        # 如果绝对时间转换失败，回退到相对UTC时间
                        time_str = seconds_to_utc_time_string(row['GPSTime'])
                        time_mode = "相对UTC时间(回退)"
                else:
                    # 模式2：无有效周数，使用相对UTC时间
                    time_str = seconds_to_utc_time_string(row['GPSTime'])
                    time_mode = "相对UTC时间"

                # 转换经纬度
                lat_dm = dms_to_dm(row['Lat_deg'], row['Lat_min'], row['Lat_sec'], False)
                lon_dm = dms_to_dm(row['Lon_deg'], row['Lon_min'], row['Lon_sec'], True)

                if lat_dm is None or lon_dm is None:
                    error_count += 1
                    continue

                # 使用H-MSL(椭球高)生成GGA语句
                gga = generate_gga(time_str, lat_dm, lon_dm, row['Q'], row['H-MSL'])

                if gga:
                    gga_sentences.append(gga)
                    success_count += 1

                    # 显示前3条的信息
                    if success_count <= 3:
                        print(f"数据生成示例 {success_count}:")
                        print(f"  GPSTime: {row['GPSTime']:.2f}")
                        print(f"  模式: {time_mode}")
                        print(f"  UTC时间字段: {time_str}")
                        print(f"  闰秒修正: -18秒")
                        print(f"  H-Ell(椭球高): {row['H-Ell']:.3f}米")
                        print(f"  H-MSL(海拔高): {row['H-MSL']:.3f}米")
                        print()
                else:
                    error_count += 1

            except Exception as e:
                error_count += 1
                if error_count <= 3:
                    print(f"警告: 第 {index + 1} 行处理失败: {e}")
                continue

        # 写入输出文件
        if gga_sentences:
            with open(output_file, 'w', encoding='utf-8') as f:
                for sentence in gga_sentences:
                    f.write(sentence + '\n')

            print(f"\n处理完成!")
            print(f"成功转换: {success_count} 条记录")
            print(f"失败记录: {error_count} 条")
            print(f"GGA语句已保存到: {output_file}")

            # 显示前几条结果
            print(f"\n前3条GGA语句示例 (使用UTC时间和椭球高):")
            for i, sentence in enumerate(gga_sentences[:3]):
                print(f"{i + 1}: {sentence}")

            # 验证时间连续性
            print(f"\n时间连续性验证:")
            times = [sentence.split(',')[1] for sentence in gga_sentences[:5]]
            for i, time_str in enumerate(times):
                print(f"  第{i + 1}条时间: {time_str}")

        else:
            print("错误: 没有生成任何GGA语句")

    except Exception as e:
        print(f"处理过程中出现错误: {e}")
        import traceback
        traceback.print_exc()

    print("\n程序结束。")


if __name__ == "__main__":
    main()