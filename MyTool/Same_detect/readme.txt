same_detect.py
###检测文本文件中的完全重复行
    该脚本用于检测文本文件中的重复行，并生成重复行的报告。

    功能：
    - 读取文本文件。
    - 检测重复行。
    - 生成重复行的报告，包括重复次数、行号和内容预览。

用法：python same_detect.py  input_file -o -v output input_file



same_detect_90.py
###可设定相似阈值的文本文件重复行
 该脚本用于检测文本文件中设定阈值的的重复行，并生成重复行的报告。

    功能：
    - 读取文本文件。
    - 检测重复行（指定相似度阈值）。
    - 生成重复行的报告，包括重复次数、行号和内容预览。
用法：python same_detect_90.py  input_file -s 0.9 -v -o output input_file
        <-s>：用于设定检测相似度阈值
