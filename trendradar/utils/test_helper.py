# coding=utf-8
"""
测试辅助工具 - 用于代码审查测试
"""

def process_data(data, config):
    """处理数据"""
    # 故意添加一些问题用于测试代码审查
    result = {}
    
    # 问题1: 潜在的空指针
    if data['items']:
        for item in data['items']:
            result[item['id']] = item['value']
    
    # 问题2: 不安全的字符串拼接
    query = "SELECT * FROM users WHERE id = " + str(data.get('user_id'))
    
    # 问题3: 硬编码的配置
    timeout = 30
    retry_count = 3
    
    return result

def validate_input(input_str):
    """验证输入"""
    # 问题: 没有处理 None 的情况
    return input_str.strip() if input_str else None

class DataProcessor:
    def __init__(self):
        self.data = None
    
    def load(self, path):
        # 问题: 没有异常处理
        with open(path, 'r') as f:
            self.data = f.read()
    
    def process(self):
        # 问题: 可能的 None 访问
        lines = self.data.split('\n')
        return [line.strip() for line in lines]
