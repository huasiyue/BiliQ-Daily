# BiliQ-Daily (B站每日一题抓取工具)

## 项目简介
BiliQ-Daily 是一个自动抓取B站特定用户「每日一题」动态并生成Markdown文档的工具。该工具专为抓取类似武忠祥老师的每日一题系列设计，能够自动识别包含"第N题"的图文动态，下载相关图片，并按照统一格式整理到Markdown文件中。

## 功能特点

- 自动识别并筛选包含"第N题"的B站动态
- 支持匿名模式和登录模式获取动态
- 自动下载题目相关图片并保存到本地
- 按照统一格式生成Markdown文档
- 智能去重，避免重复抓取已处理的动态
- 支持自定义配置（目标UID、输出路径等）

## 安装步骤

### 前置要求

- Python 3.7+
- pip (Python包管理器)

### 安装依赖

```bash
pip install -r requirements.txt
```

如果没有requirements.txt文件，请安装以下依赖：

```bash
pip install bilibili-api-python requests
```

## 配置说明

在项目根目录创建`config.json`文件，参考以下格式：

```json
{
  "target_uid": "你要抓取的B站用户UID",
  "output_md_file": "输出的Markdown文件名.md",
  "image_dir": "图片保存目录",
  "login": {
    "sessdata": "你的SESSDATA（可选）",
    "bili_jct": "你的bili_jct（可选）",
    "buvid3": "你的buvid3（可选）"
  }
}
```

### 配置项说明

- `target_uid`: 必填，要抓取的B站用户UID
- `output_md_file`: 必填，输出的Markdown文件名
- `image_dir`: 必填，图片保存目录
- `login`: 可选，B站登录凭证，用于获取需要登录才能查看的动态
  - `sessdata`, `bili_jct`, `buvid3`: B站Cookie中的对应值

## 使用方法

1. 配置好`config.json`文件
2. 运行脚本：

```bash
python biliq_daily.py
```

3. 脚本会自动抓取目标用户的动态，筛选出包含"第N题"的内容，下载图片并生成Markdown文档

## 示例输出

脚本会生成类似以下格式的Markdown文档：

```markdown
<!-- ID: 123456789 -->
## 每日一题 | 第 42 题 (2023-01-01 12:00)

**文本:**

第42题：...(题目内容)

**图片:**

![每日一题 | 第 42 题](bili_images/42_2023_01_01.jpg)

---
```

## 注意事项

- 图片会以`题号_年_月_日.扩展名`的格式保存在指定目录
- 脚本会自动跳过已处理过的动态，避免重复
- 如需访问限制级动态或提高API访问限制，请配置登录信息
