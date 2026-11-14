# 药品 SQLite 数据库

`medicines.sqlite3` 由 `doc/medicines.csv` 与 `images/` 生成。保留 CSV 全部字段，PZN 使用 TEXT 主键，保留前导零。

- `medicines`：每个药品一行，包含名称、成分、剂量、颜色、形状、生产商、来源和核查状态等原始信息。
- `medicine_images`：通过外键 `pzn` 关联药品，`kind` 区分 `tablet_image_1`、`tablet_image_2` 和 `package_image`，`path` 相对 `images/`。
- 图片保留为原始文件，不存 BLOB；分发时请同时保留数据库与图片目录。CSV 的图片字段也保留原始值。
- 无图片的药品仍可查询，结果的 `images` 为 `[]`。未凭空补图，也未进行图像识别或医学核验。说明书路径保留 CSV 原值，相对 `images/`。

## 生成和查询

在项目根目录执行：

```bash
conda activate hackathon
python -m pip install -r requirements.txt
conda run -n hackathon python -m app.medicine_catalog build
conda run -n hackathon python -m app.medicine_catalog search --pzn 06865959
conda run -n hackathon python -m app.medicine_catalog search Bisoprolol
```

导入检查重复/非法 PZN、图片文件是否存在以及图片目录与 PZN 是否匹配；全部成功后才替换旧数据库。修改源 CSV 或图片后重新执行 `build`。

```sql
SELECT m.pzn, m.product_name, m.active_ingredient, m.strength_as_stated,
       i.kind, i.path
FROM medicines AS m
LEFT JOIN medicine_images AS i ON i.pzn = m.pzn
WHERE m.pzn = '06865959';
```

## Flask API

运行 `conda run -n hackathon python run.py` 后：

```text
GET /api/medicines?q=Bisoprolol
GET /api/medicines?pzn=06865959
GET /api/medicines?active_ingredient=Bisoprolol&color=yellow
GET /api/medicines?shape=round&limit=10&offset=0
GET /api/medicines/06865959
```

列表结果包含 `total`、`limit`、`offset`、`results`。每条药品包含所有 CSV 字段，以及 `images: [{id, kind, path, url}]`。访问 `url`（如 `/api/medicines/images/1`）获取实际图片。

`q` 仅在 `product_name`、`active_ingredient`、`pzn` 三个字段中进行不区分大小写的前缀搜索（从字段首字符开始连续匹配）；其他 CSV 字段名可作为筛选参数，多个条件同时满足。`pzn` 精确匹配，数字不足 8 位会补零。默认每页 20 条，最多 200 条。此数据规模下使用扫描查询实现 Unicode 大小写匹配。图片缺失以 HTTP 404 返回。

药品目录 API 是公开的只读目录，不包含患者数据。数据库不存在时返回 503，不自动修改源数据。可通过 `MEDLINK_MEDICINES_DB` 环境变量指定其他数据库位置。

验证：`conda run -n hackathon python -m unittest discover -s tests -v`
