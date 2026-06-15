"""去AI味分层处理：L1规则清洗 → L2句法调优 → L3创意润色 → L4实体锚定校验。"""

from anti_ai.pipeline import run_anti_ai_pipeline, AntiAIReport
from anti_ai.layer1_clean import clean_layer1
from anti_ai.layer2_syntax import tune_layer2
from anti_ai.layer3_llm import polish_layer3
from anti_ai.layer4_validate import validate_layer4

__all__ = [
    "run_anti_ai_pipeline",
    "AntiAIReport",
    "clean_layer1",
    "tune_layer2",
    "polish_layer3",
    "validate_layer4",
]
