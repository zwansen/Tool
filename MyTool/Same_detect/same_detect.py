import sys
import argparse
import os
from datetime import datetime

def check_duplicates():
    parser = argparse.ArgumentParser(description='检测文本文件中的重复行')
    parser.add_argument('file', help='要检测的文件路径')
    parser.add_argument('-v', '--verbose', action='store_true', help='显示详细信息')
    parser.add_argument('-o', '--output', help='输出结果到指定文件，不指定则输出到控制台和默认文件')
    
    args = parser.parse_args()
    
    # 在try块外部定义变量
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_name = os.path.basename(args.file)
    name_without_ext = os.path.splitext(base_name)[0]
    
    try:
        # 读取文件
        with open(args.file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        # 检测重复行
        seen = {}
        duplicates = {}
        
        for i, line in enumerate(lines, 1):
            line = line.rstrip('\n')
            if line in seen:
                if line not in duplicates:
                    duplicates[line] = [seen[line]]
                duplicates[line].append(i)
            else:
                seen[line] = i
        
        # 准备输出内容
        output_lines = []
        output_lines.append("=" * 60)
        output_lines.append(f"重复行检测报告")
        output_lines.append(f"检测文件: {args.file}")
        output_lines.append(f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output_lines.append(f"文件总行数: {len(lines)}")
        output_lines.append(f"去重后行数: {len(seen)}")
        output_lines.append("=" * 60)
        
        if duplicates:
            output_lines.append(f"\n发现 {len(duplicates)} 组重复行:\n")
            
            for line, line_nums in duplicates.items():
                output_lines.append(f"{'='*40}")
                output_lines.append(f"重复 {len(line_nums)} 次 (行号: {line_nums})")
                
                if args.verbose:
                    # 显示完整内容
                    output_lines.append(f"内容: {line}")
                else:
                    # 显示前150个字符
                    preview = line[:150] + "..." if len(line) > 150 else line
                    output_lines.append(f"内容预览: {preview}")
                
                # 如果是Eph行，解析关键信息
                if line.startswith('Eph,'):
                    try:
                        parts = line.split(',')
                        if len(parts) >= 4:
                            output_lines.append(f"关键信息: Eph[{parts[1]}, {parts[2]}, {parts[3]}]")
                    except:
                        pass
                
                output_lines.append("")
        else:
            output_lines.append("\n✓ 未发现重复行")
        
        # 统计信息
        output_lines.append("\n" + "=" * 60)
        output_lines.append("统计信息:")
        output_lines.append(f"总重复行数: {sum(len(nums) for nums in duplicates.values()) - len(duplicates)}")
        output_lines.append(f"重复率: {(sum(len(nums) for nums in duplicates.values()) - len(duplicates)) / len(lines) * 100:.2f}%")
        output_lines.append("=" * 60)
        
        # 组合输出内容
        output_content = "\n".join(output_lines)
        
        # 输出到控制台
        print(output_content)
        
        # 输出到文件
        if args.output:
            output_file = args.output
        else:
            # 使用已定义的name_without_ext
            output_file = f"duplicate_report_{name_without_ext}_{timestamp}.txt"
        
        with open(output_file, 'w', encoding='utf-8') as f:  # 修改这里
            f.write(output_content)
        
        print(f"\n检测报告已保存到: {output_file}")
        
        # 如果发现重复行，也生成一个简化的CSV文件便于查看
        if duplicates:
            csv_file = f"duplicate_summary_{name_without_ext}_{timestamp}.csv"
            with open(csv_file, 'w', encoding='utf-8') as f:
                f.write("重复次数,行号,内容预览\n")
                for line, line_nums in duplicates.items():
                    preview = line[:100].replace(',', ';') if len(line) > 100 else line.replace(',', ';')
                    f.write(f"{len(line_nums)},\"{','.join(map(str, line_nums))}\",\"{preview}\"\n")
            print(f"重复行摘要已保存到: {csv_file}")

        return {
            "total_lines": len(lines),
            "unique_lines": len(seen),
            "similarity": None,
            "exact_groups": [{"lines": v, "content": k} for k, v in duplicates.items()],
            "similar_groups": [],
        }

    except FileNotFoundError:
        print(f"错误: 文件 '{args.file}' 不存在")
    except Exception as e:
        print(f"处理文件时出错: {e}")

if __name__ == "__main__":
    check_duplicates()