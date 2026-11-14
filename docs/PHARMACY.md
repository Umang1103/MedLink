# 医生与药房工作流

## 环境与启动

本次实现使用 conda 的 `hackathon` 环境（Python 3.11），不需要 `.venv`。

```bash
conda activate hackathon
python -m pip install -r requirements.txt
python run.py
```

如果 5000 端口被占用：

```bash
conda run -n hackathon python -m flask --app run:app run --port 5055
```

## 登录与账户存储

医生和药房共用 `/login`，服务器从账号库读取角色，登录后自动进入对应界面：

| 演示角色 | 用户名 | 密码 |
| --- | --- | --- |
| Doctor | `dr.demo` | `demo1234` |
| Pharmacy | `pharmacy.demo` | `pharmacy1234` |

账号存放在 `instance/accounts.sqlite3` 的 `users` 表：`username`、`name`、`role`、`password_hash`、`active`。密码采用带盐哈希存储，登录时通过 Werkzeug 校验，不保存用户的明文密码。演示账号的默认凭据作为初始化配置写在 `app/auth.py` 中；仅在账号不存在且启用演示初始化时创建，不覆盖已有密码。当前 conda 环境使用 scrypt。

创建正式账户，密码交互输入且不回显：

```bash
conda run --no-capture-output -n hackathon python -m flask --app run:app create-user
```

选择 `doctor` 或 `pharmacy` 角色。密码至少 10 个字符。设置 `MEDLINK_SEED_DEMO=0` 可以阻止之后创建演示账号；**不会删除或禁用已经存在的演示账号**。管理员可在账号库中将对应账号 `active` 设为 `0`，下一次请求即失去登录权限。会话只保存账号标识，角色每次从数据库读取。

## 药房操作

1. 使用 pharmacy 账号登录，进入 `/pharmacy/`。
2. 输入患者 KVNR、完整姓名与出生日期。三项匹配后显示该患者的处方；更换患者或匹配失败会清除原来的访问范围。
3. 处方按原始开方顺序编号 `01`、`02`……。每项需输入实际取出的药品 PZN，或者让扫码枪向输入框写入条码。提交后会显示解析出的 PZN 和实际药名以供核对。
4. 支持直接 8 位 PZN、`-PZN`、`PZN: XXXXXXXX` 和以 `4150` 开头且校验通过的 13 位条码。**不把任意 GTIN、GS1 或 PPN 当作 PZN 猜测**；不支持的编码请填写包装上印刷的 PZN。
5. “Scan with camera” 使用浏览器 `BarcodeDetector` 识别摄像头条码；需浏览器支持、摄像头授权及 HTTPS 或 localhost。不可用时支持扫码枪和手动输入。扫描后仍须核对并点击确认，不自动发药。
6. 如果实际 PZN 不同，必须填写替换原因，且药师确认有效成分、规格、剂型和给药适用性。系统要求成分名称一致；医学同义名/盐型差异需先与医生确认。系统不自动判定临床可替换性。原药品资料一直保留。
7. 未收录的药品必须填写名称及文字描述或照片；替换药品还必须有相同有效成分。可补充规格、剂型、形状、颜色和用途。照片按钮在支持的手机上调用相机，桌面可上传 JPEG/PNG/WebP。图片会验证、缩小并重新编码为 JPEG，不保留原始 EXIF。
8. 每种药独立点击 “Confirm medicine dispensed”。所有项完成后点击 “Complete dispensing & generate patient QR”。同一处方只能由开始处理的药房账号编辑；完成后锁定本轮记录；可点击 Refill 开始新一轮发药，必须重新逐项确认。上一轮归档保留，旧二维码及照片继续对应旧清单，新一轮完成后生成独立二维码。

药房补充的用途/描述标明为药房提供的信息；基础药品库中的资料不会被覆盖。没有图片但有描述时最终页面显示描述及“暂无照片”。

## 两种最终清单

**医生**：原来的处方详情 `/prescribe/<id>` 自动展示实际发药内容、替换标记、原 PZN → 实际 PZN、原因、补充资料、图片和药房账号。保留原始药品顺序和编号，即便替换也不重新编号。当前医生只能查看自己开出的处方。

**患者**：药房完成后展示二维码和 `/medications/<随机令牌>` 链接。页面仅投影最终发出的药品（PZN、名称、图片/描述、成分、用途及用法），显示 SUBSTITUTED 替换标记，但没有原品牌、原 PZN 或替换原因，也不显示姓名、KVNR 和出生日期。两端的 `01`、`02` 等编号相同。二维码在服务器本地生成，不调用外部二维码服务。

二维码是访问凭据，持有链接的人可以查看该最终清单。私有照片只有有权限的医药账号或对应患者链接才能访问，不能通过文件名直接下载。尚未完成的处方不生成患者令牌。

手机扫码时，`127.0.0.1` 指向手机本身。请把 `MEDLINK_PUBLIC_BASE_URL` 设置为手机实际可访问的应用 HTTPS 地址，例如：

```bash
export MEDLINK_PUBLIC_BASE_URL=https://your-medlink-host.example
```

该变量只影响二维码和链接的目标域名，不负责部署、开放网络或配置 HTTPS。

## 数据分层与权限

| 位置 | 内容 |
| --- | --- |
| `instance/accounts.sqlite3` | 医生/药房账号、角色、带盐密码哈希、启用状态 |
| `instance/prescriptions.sqlite3` | 原有患者和处方，以及新增 `dispensing`、`dispensed_items`；记录替换标记、实际药、补充说明、照片引用和患者令牌 |
| `instance/dispensing_photos/` | 药房补拍且已重新编码的图片，非静态公开目录 |
| `instance/pending_medicines.sqlite3` | `pending_medicines` 待补充记录，含 PZN、药名、成分、描述、照片引用、提交者和 `status=pending`；不含姓名、KVNR、处方 ID |
| `Hackthon/data/medicines.sqlite3` | 原始药品目录，本流程只读，不改动 |

待补充库目前用于后续人工/药物公司审核，不提供公司直接访问患者处方的入口，也不自动将未审核资料合入基础库。照片仍与临床记录共用私有文件存储，后续导出审核资料应按审核用途挑选文件。

权限由服务端装饰器及逐条记录校验实现，隐藏按钮不作为权限控制。Doctor 可以开方、看自己的处方；Pharmacy 只能在身份匹配后查看和处理该患者处方，不能调用医生开方接口。患者链接仅开放最终清单和相应图片，不开放处方查询、替换历史或数据库文件。补充库通过 SQLite ATTACH 与发药明细在同一个事务中写入；生成最终清单前要求所有药品已确认，并在写锁下检查是否已完成。

这些是应用层权限；SQLite 文件由运行服务的操作系统账号读取。数据库文件不放入静态目录，`instance/` 已在 `.gitignore` 中。部署时需由服务器管理员限制文件系统权限，并设置稳定的 `MEDLINK_SECRET_KEY`。

路径可用 `MEDLINK_ACCOUNTS_DB`、`MEDLINK_PRESCRIPTIONS_DB` 和 `MEDLINK_PENDING_DB` 环境变量覆盖。备份时同时保留账号库、处方库、待审核库和私有照片目录。

## 验证

```bash
conda run -n hackathon python -m unittest discover -s tests -v
```

覆盖角色隔离、三项患者匹配、账号哈希/禁用、创建账号命令、PZN/条码解析、未知药品补充与照片、基础库保持不变、医生替换标记、患者内容隔离、两端排序、QR 目标地址和完成后锁定。

## Refill 与状态显示

医生端 `SUBSTITUTED` 为红色，`Dispensed as prescribed` 为绿色；同时展示原药和实际发药的资料与图片。患者端也显示替换状态，但只展示实际取得药品的信息。药品顺序和编号不变。

完成发药后，药房详情页的 **Refill · start new dispensing** 可开启下一轮。新增 `dispensing_history` 表保存上一轮完整快照及二维码令牌，不删除历史或照片。Refill 未完成时，医生端继续展示上一轮已完成记录并标明正在 Refill；完成后更新主清单标记，历史记录可展开查看。旧 QR 永远保持对应旧轮次。

**Take pill photo** 独立按钮可直接打开摄像头，点击 **Capture photo** 拍摄并预览，然后随药品确认保存；**Close camera** 可停止相机。支持上传已有照片；摄像头需要权限和 HTTPS/localhost，不支持时回退到文件/设备相机选择器。

## 不完整表单保留

药房提交单项药品后，如果缺少替换原因、有效成分等资料，已填文字、勾选状态和通过格式校验的图片会保存到 `prescriptions.sqlite3` 的 `dispensing_drafts` 表。草稿按药品项与药房账号隔离，刷新或返回同一处方可继续补齐；预览图片同样需要该药房账号及匹配患者身份。再次提交可直接复用已保存图片，无需重新上传或拍摄。确认成功时清除对应草稿，并把图片关联到正式发药记录。损坏或不支持的图片仍需要重新选择。

医生端未替换的药品只显示一份药品资料及绿色发药状态；只有替换项显示原药与实际药品对照。

## Open patient 与独立打印

医生通过 Open patient 输入 KVNR 后，在 **Medicine Identification** 中也能查看该患者由自己开具的处方。此页面与 Prescriptions 详情共用展示逻辑，包含未发药、发药中、已发药、Refill 状态，替换前后资料、图片和历史记录。只列出所选患者且属于当前医生账号的处方。

药房完成发药后，主页面继续显示二维码。**Print QR** 在新页面打印二维码和访问链接，**Print list** 在新页面打印最终患者药品清单（含图片、用法和替换标记，无原药资料或内部替换原因）。打印页等待图片加载后打开打印对话框，也保留手动打印按钮。两种打印页面都要求药房登录、匹配当前患者且该轮发药已完成。

## 患者药品详细资料

患者二维码页面与 Print list 共用详细药品卡片，按实际发出药品显示已有信息：药名、PZN、有效成分、成分形式、规格、剂型、品牌、包装数量、厂家，以及颜色、形状、刻印、刻痕和原始外观描述（包含来源中已有的尺寸描述）。同时保留图片、药房补充说明、用途、每次用量和每天次数。药品规格与患者服用次数分开显示；不推算未提供的尺寸或有效成分总摄入量。

新发药记录在 `dispensed_items.medicine_details_json` 保存识别资料快照，Refill 历史一并保存。老记录没有快照时，按实际发出的 PZN 查询基础目录补充显示；替换药不会套用原处方药的外形或品牌资料。缺失字段不编造，也不显示空值。

## 医生首页与患者视角

医生登录后进入双入口首页：**Patient records** 用于查找和查看记录，**Prescribe** 用于开方。Patient records 支持 KVNR 精确查找，或姓名（不区分大小写、部分匹配）查找；姓名匹配会列出姓名、生日和 KVNR，由医生明确选择，避免误打开同名患者。

患者记录中的 **Prescription** 标签替代原 Medicine Identification。新增 **Patient version**，按当前患者展示当前医生所开处方的最新已完成发药清单，与患者扫码后使用相同的药品卡片、用量、外观和图片，只显示实际发出的药品。未完成首轮发药时提示暂无最终清单；Refill 进行中仍显示上一轮已取药清单，完成后更新为新一轮。替换原因和原药对照仍在 Prescription 标签中查看。
