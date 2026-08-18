### 测试语句时间连续性，检测语句（GGA、RMC、PVTResult、PVTMeas）的时间或bb_tag的连续性
### 根据连续性可看出是否存在数据包丢失现象

使用方法：python time_continuity.py input_file output_file [--freq {1|10}]

参数说明：
  input_file    输入的GPS日志文件路径
  output_file   输出结果文件路径
  --freq        数据输出频率（Hz），可选 1 或 10，默认 10

示例：
  python time_continuity.py log.txt result.txt
  python time_continuity.py log.txt result.txt --freq 1

检测内容：
- GNRMC/GPRMC 时间的连续性
- GNGGA/GPGGA 时间的连续性（支持定位状态显示）
- PVTResult 的 bb_tag 和时间连续性
- PVTMeas 的 bb_tag 和 gps_rcv_time 连续性

改进特性：
1. GGA-RMC时间反向匹配：GGA本身不含日期，代码会优先使用顺序RMC的日期。
   当RMC在GGA后面（或顺序日期异常）时，会根据GGA的HHMMSS与附近RMC做时间
   比对（容差2秒），匹配成功则借用该RMC的日期。

2. 一行多语句/前缀污染处理：如果日志中存在一行多个NMEA语句（粘包），或
   语句前面有额外字符（如"[DEBUG] $GPRMC,..."），代码会自动拆分并提取有效
   语句进行解析。

3. 频率参数化：通过 --freq 参数自动调整检测阈值，无需手动修改代码：
   - 1Hz：时间间隔1s，容差1.1s
   - 10Hz（默认）：时间间隔0.1s，容差0.11s

==========================================PVTResut和PVTMeas打印格式============================================================
void print_pvt()
{
    // ===================== PVT 输出（头尾分隔）=====================
        // 1. 输出 PVT 头
        memset(print_buffer, 0, sizeof(print_buffer));
        sprintf(print_buffer, "$PVTResult,%d,%d%02d%02d-%02d%02d%02d.%03d,%d,%f,%f,%f\r\n",
            g_pvt_result.bb_tag,
            g_pvt_result.year+2000, g_pvt_result.month, g_pvt_result.day, g_pvt_result.hour, g_pvt_result.minute, g_pvt_result.second, g_pvt_result.milli_second,
            g_pvt_result.pos_status,
            g_pvt_result.pos_ecef_x,
            g_pvt_result.pos_ecef_y,
            g_pvt_result.pos_ecef_z);
        ks_driver_uart_send_string(0, print_buffer);
        // ===================================================================
}

void print_pvt_meas()
{
    // ===================== PVT 观测量输出（头尾分隔）=====================
        // 1. 输出 PVT 观测量头
        if (g_pvt_meas.antenna_index == 0)
        {
            memset(print_buffer, 0, sizeof(print_buffer));
            sprintf(print_buffer, "$PVTMeas,%d,%d,%d,%f,%d,%d,%d\r\n",
                g_pvt_meas.bb_tag,
                g_pvt_meas.gps_week+2048,
                g_pvt_meas.gps_local_time_ms,
                g_pvt_meas.gps_rcv_time,
                g_pvt_meas.gps_time_adjust_ms,
                g_pvt_meas.gps_time_state,
                g_pvt_meas.meas_count
            );
            ks_driver_uart_send_string(0, print_buffer);
        }
        // ===================================================================
}
