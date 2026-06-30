"""Data 相关的 Pydantic 模型"""

from pydantic import BaseModel, Field


class ScanDirectoryRequest(BaseModel):
    """扫描目录"""
    path: str = ""


class GenerateDataSpecRequest(BaseModel):
    """NL + 目录信息 → MD 数据集描述"""
    description: str = ""
    directory_path: str = Field(default="", alias="directoryPath")
    files: list[dict] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class RegisterDatasetRequest(BaseModel):
    """注册数据集"""
    spec_md: str = Field(alias="specMd")
    dataset_id: str = Field(default="", alias="datasetId")
    dataset_name: str = Field(default="", alias="datasetName")
    data_path: str = Field(default="", alias="dataPath")
    file_count: int = 0
    total_size: int = 0
    formats: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class MatchToolsRequest(BaseModel):
    """匹配数据集的可用工具"""
    dataset_id: str = Field(default="", alias="datasetId")
    request: str = ""  # 用户处理需求


class PreviewRequest(BaseModel):
    """预览数据集"""
    dataset_id: str = Field(default="", alias="datasetId")
