import sys
import argparse
import os
from datetime import datetime
from difflib import SequenceMatcher

def similar(a, b):
    """计算两个字符串的相似度"""
    return SequenceMatcher(None, a, b).ratio()

def check_duplicates():
    parser = argparse.ArgumentParser(description='检测文本文件中的重复行')
    parser.add_argument('file', help='要检测的文件路径')
    parser.add_argument('-v', '--verbose', action='store_true', help='显示详细信息')
    parser.add_argument('-o', '--output', help='输出结果到指定文件，不指定则输出到控制台和默认文件')
    parser.add_argument('-s', '--similarity', type=float, default=None, 
                       help='启用相似度检测，设置相似度阈值（0.0-1.0）')
    
    args = parser.parse_args()
    
    # 验证阈值
    if args.similarity is not None and (args.similarity < 0 or args.similarity > 1):
        print(f"错误: 相似度阈值必须在0.0到1.0之间，当前为{args.similarity}")
        return
    
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
        
        # 检测相似重复行（如果启用了相似度检测）
        similar_duplicates = {}
        if args.similarity is not None:
            # 创建一个字典，存储行号和对应的行内容
            line_dict = {}
            for i, line in enumerate(lines, 1):
                line_dict[i] = line.rstrip('\n')
            
            # 获取所有不重复的行号
            unique_line_indices = list(seen.values())  # 获取所有唯一行的行号
            processed = set()
            
            for i in range(len(unique_line_indices)):
                idx1 = unique_line_indices[i]
                
                # 如果这行已经处理过，跳过
                if idx1 in processed:
                    continue
                    
                similar_group = [idx1]
                line1 = line_dict[idx1]
                
                for j in range(i + 1, len(unique_line_indices)):
                    idx2 = unique_line_indices[j]
                    
                    # 跳过已处理的
                    if idx2 in processed:
                        continue
                    
                    line2 = line_dict[idx2]
                    
                    # 计算相似度
                    if similar(line1, line2) >= args.similarity:
                        similar_group.append(idx2)
                        processed.add(idx2)
                
                # 如果找到相似行（不仅仅是自己）
                if len(similar_group) > 1:
                    similar_duplicates[idx1] = similar_group.copy()
                    processed.update(similar_group)
        
        # 准备输出内容
        output_lines = []
        output_lines.append("=" * 60)
        output_lines.append(f"重复行检测报告")
        output_lines.append(f"检测文件: {args.file}")
        output_lines.append(f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if args.similarity is not None:
            output_lines.append(f"相似度阈值: {args.similarity*100:.0f}%")
        output_lines.append(f"文件总行数: {len(lines)}")
        output_lines.append(f"去重后行数: {len(seen)}")
        output_lines.append("=" * 60)
        
        has_duplicates = False
        
        # 显示完全相同的重复行（保持原有格式）
        if duplicates:
            has_duplicates = True
            output_lines.append(f"\n发现 {len(duplicates)} 组完全相同的重复行:\n")
            
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
        
        # 显示相似重复行
        if similar_duplicates:
            has_duplicates = True
            output_lines.append(f"\n{'='*60}")
            output_lines.append(f"发现 {len(similar_duplicates)} 组相似重复行（相似度≥{args.similarity*100:.0f}%）:\n")
            
            for rep_idx, line_nums in similar_duplicates.items():
                rep_line = line_dict[rep_idx]
                output_lines.append(f"{'-'*40}")
                output_lines.append(f"相似重复 {len(line_nums)} 次 (行号: {line_nums})")
                output_lines.append(f"代表行: {rep_line[:150] + '...' if len(rep_line) > 150 else rep_line}")
                
                if args.verbose:
                    output_lines.append("\n相似行详情:")
                    for line_num in line_nums:
                        line_content = line_dict[line_num]
                        output_lines.append(f"  行{line_num}: {line_content}")
                output_lines.append("")
        
        if not has_duplicates:
            output_lines.append("\n✓ 未发现重复行")
        
        # 统计信息
        output_lines.append("\n" + "=" * 60)
        output_lines.append("统计信息:")
        
        # 计算完全重复的统计
        exact_duplicate_count = 0
        if duplicates:
            exact_duplicate_count = sum(len(nums) for nums in duplicates.values()) - len(duplicates)
        
        # 计算相似重复的统计
        similar_duplicate_count = 0
        if similar_duplicates:
            for line_nums in similar_duplicates.values():
                similar_duplicate_count += len(line_nums) - 1  # 减去代表行本身
        
        output_lines.append(f"完全重复行数: {exact_duplicate_count}")
        if exact_duplicate_count > 0:
            output_lines.append(f"完全重复率: {exact_duplicate_count / len(lines) * 100:.2f}%")
        
        if args.similarity is not None:
            output_lines.append(f"相似重复行数: {similar_duplicate_count}")
            if similar_duplicate_count > 0:
                output_lines.append(f"相似重复率: {similar_duplicate_count / len(lines) * 100:.2f}%")
        
        # 总计
        total_duplicates = exact_duplicate_count + similar_duplicate_count
        output_lines.append(f"总计重复行数: {total_duplicates}")
        if total_duplicates > 0:
            output_lines.append(f"总计重复率: {total_duplicates / len(lines) * 100:.2f}%")
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
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(output_content)
        
        print(f"\n检测报告已保存到: {output_file}")
        
        # 如果发现重复行，也生成一个简化的CSV文件便于查看
        if has_duplicates:
            csv_file = f"duplicate_summary_{name_without_ext}_{timestamp}.csv"
            with open(csv_file, 'w', encoding='utf-8') as f:
                f.write("重复类型,重复次数,行号,内容预览\n")
                
                # 写入完全重复
                for line, line_nums in duplicates.items():
                    preview = line[:100].replace(',', ';') if len(line) > 100 else line.replace(',', ';')
                    f.write(f"完全重复,{len(line_nums)},\"{','.join(map(str, line_nums))}\",\"{preview}\"\n")
                
                # 写入相似重复
                for rep_idx, line_nums in similar_duplicates.items():
                    rep_line = line_dict[rep_idx]
                    preview = rep_line[:100].replace(',', ';') if len(rep_line) > 100 else rep_line.replace(',', ';')
                    f.write(f"相似重复,{len(line_nums)},\"{','.join(map(str, line_nums))}\",\"{preview}\"\n")
            
            print(f"重复行摘要已保存到: {csv_file}")

        return {
            "total_lines": len(lines),
            "unique_lines": len(seen),
            "similarity": args.similarity,
            "exact_groups": [{"lines": v, "content": k} for k, v in duplicates.items()],
            "similar_groups": [{"lines": v, "rep": line_dict[k]} for k, v in similar_duplicates.items()],
        }

    except FileNotFoundError:
        print(f"错误: 文件 '{args.file}' 不存在")
    except Exception as e:
        print(f"处理文件时出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_duplicates()