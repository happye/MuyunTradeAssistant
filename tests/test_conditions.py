# -*- coding: utf-8 -*-
"""ISS-002 参数化条件测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING)

from src.data.models import StockData
from src.core.skill_engine import YAMLBasedSkill

def test_parameterized_conditions():
    # 测试数据: 微跌0.5%
    test_data = StockData(
        stock_code='600519', stock_name='test', price=1500.0,
        ma5=1480.0, ma20=1450.0, ma60=1400.0,
        volume=2000000, avg_volume_20=1000000,
        change_pct=-0.5,
        high_60d=1600.0, low_60d=1300.0,
        macd_dif=5.0, macd_dea=3.0, macd_hist=2.0,
        rsi_6=45.0, rsi_12=50.0, rsi_24=55.0,
        boll_upper=1550.0, boll_mid=1450.0, boll_lower=1350.0,
        kdj_k=65.0, kdj_d=55.0, kdj_j=85.0,
    )

    # 测试1: change_negative 带阈值 — 微跌-0.5%不超过-1%阈值，不应触发
    config1 = {
        'name': 'test1', 'alias': 'threshold_test1',
        'rules': [{
            'condition': {
                'change_negative': {'max': -1.0, 'reason': '跌超1%才算真正下跌'},
            },
            'signal': 'SELL', 'confidence': 0.8, 'weight': 1.0,
        }]
    }
    skill1 = YAMLBasedSkill('test1', config1, None)
    sig1 = skill1.execute(test_data)
    print(f'Test1 - change_negative(max=-1.0) on change=-0.5%: signal={sig1.signal.value}, reasons={sig1.reason}')
    assert sig1.signal.value == 'WATCH', f'Expected WATCH, got {sig1.signal.value}'

    # 测试2: change_negative 不带阈值 — 微跌也应触发
    config2 = {
        'name': 'test2', 'alias': 'threshold_test2',
        'rules': [{
            'condition': {
                'change_negative': {'reason': '只要跌就算'},
            },
            'signal': 'SELL', 'confidence': 0.8, 'weight': 1.0,
        }]
    }
    skill2 = YAMLBasedSkill('test2', config2, None)
    sig2 = skill2.execute(test_data)
    print(f'Test2 - change_negative(no threshold) on change=-0.5%: signal={sig2.signal.value}, reasons={sig2.reason}')
    assert sig2.signal.value == 'SELL', f'Expected SELL, got {sig2.signal.value}'

    # 测试3: volume_ratio 带倍数
    config3 = {
        'name': 'test3', 'alias': 'volume_test',
        'rules': [{
            'condition': {
                'volume_ratio': {'above': 1.5, 'reason': '量比超过1.5倍'},
            },
            'signal': 'BUY', 'confidence': 0.8, 'weight': 1.0,
        }]
    }
    skill3 = YAMLBasedSkill('test3', config3, None)
    sig3 = skill3.execute(test_data)
    print(f'Test3 - volume_ratio(above=1.5) on ratio=2.0: signal={sig3.signal.value}, reasons={sig3.reason}')
    assert sig3.signal.value == 'BUY', f'Expected BUY, got {sig3.signal.value}'

    # 测试4: ma_distance 偏离均线
    config4 = {
        'name': 'test4', 'alias': 'distance_test',
        'rules': [{
            'condition': {
                'ma_distance': {'ma': 'ma20', 'above': 3.0, 'reason': '偏离MA20超过3%'},
            },
            'signal': 'SELL', 'confidence': 0.7, 'weight': 1.0,
        }]
    }
    skill4 = YAMLBasedSkill('test4', config4, None)
    sig4 = skill4.execute(test_data)
    # price=1500, ma20=1450, dist = (1500-1450)/1450*100 = 3.45% > 3.0
    print(f'Test4 - ma_distance(above=3.0) on dist=3.45%: signal={sig4.signal.value}, reasons={sig4.reason}')
    assert sig4.signal.value == 'SELL', f'Expected SELL, got {sig4.signal.value}'

    # 测试5: change_range 暗度陈仓（微涨0~2%+放量）
    test_data2 = StockData(
        stock_code='000001', stock_name='test2', price=10.5,
        volume=2000000, avg_volume_20=1000000, change_pct=0.8,
    )
    config5 = {
        'name': 'test5', 'alias': 'stealth_accumulation',
        'rules': [{
            'condition': {
                'change_range': {'min': 0.0, 'max': 2.0, 'reason': '微涨0~2%'},
                'volume_ratio': {'above': 1.5, 'reason': '放量1.5倍以上'},
            },
            'signal': 'BUY', 'confidence': 0.7, 'weight': 1.0,
        }]
    }
    skill5 = YAMLBasedSkill('test5', config5, None)
    sig5 = skill5.execute(test_data2)
    print(f'Test5 - stealth accumulation(change=0.8%, ratio=2.0): signal={sig5.signal.value}, reasons={sig5.reason}')
    assert sig5.signal.value == 'BUY', f'Expected BUY, got {sig5.signal.value}'

    # 测试6: rsi_above 通用条件
    config6 = {
        'name': 'test6', 'alias': 'rsi_test',
        'rules': [{
            'condition': {
                'rsi_above': {'period': 6, 'threshold': 40, 'reason': 'RSI6高于40'},
            },
            'signal': 'HOLD', 'confidence': 0.6, 'weight': 1.0,
        }]
    }
    skill6 = YAMLBasedSkill('test6', config6, None)
    sig6 = skill6.execute(test_data)  # rsi_6=45.0 > 40
    print(f'Test6 - rsi_above(period=6, threshold=40) on rsi6=45: signal={sig6.signal.value}, reasons={sig6.reason}')
    assert sig6.signal.value == 'HOLD', f'Expected HOLD, got {sig6.signal.value}'

    print()
    print('ALL PARAMETERIZED CONDITION TESTS PASSED!')

if __name__ == '__main__':
    test_parameterized_conditions()
