# VibraNews

振动光谱（Raman、SRS/CARS、SERS/TERS、布里渊、IR/NIR、光热/光声红外、太赫兹与低波数、SFG/2D-IR/FSRS、ROA/VCD 等）社区周报网站，参照 OCT News 的形式：**每天自动抓取并发布新论文**，按周归为一期（每周一开新一期），编辑随时可以修改、删除、选特稿。

- 网站：纯静态页面（`index.html`），托管在 GitHub Pages，读取 `data/issues/*.json`
- 抓取：`scraper/vibranews_scraper.py`，数据源为 Europe PMC、Crossref、arXiv（免费接口，不需要 API key）
- 自动化：GitHub Actions，每天新加坡时间 06:23 运行（`daily-update.yml`），抓过去 3 天的新论文，直接发布

---

## 一次性设置（约 10 分钟）

1. **新建仓库**：在 GitHub 新建公开仓库，例如 `vibranews`，把本目录全部内容推上去（默认分支 `main`）。
2. **打开 Pages**：仓库 *Settings → Pages → Build and deployment → Source* 选 **GitHub Actions**。
3. **允许工作流写入和开 PR**：*Settings → Actions → General → Workflow permissions* 选 **Read and write permissions**，并勾选 **Allow GitHub Actions to create and approve pull requests**。
4. **填网址**：把 `scraper/config.yaml` 里的 `site.url` 改成你的 Pages 地址（本站为 `https://forestbin-raman.github.io/VibraNews/`）。它只用于抓取时的 User-Agent，表明来源。
5. **第一次发布**：推送到 `main` 会自动运行 *Deploy site to GitHub Pages*，几分钟后网站上线，显示第 1 期。
6. **手动试跑每日抓取**：*Actions → Daily update → Run workflow*。

## 日常流程

1. **每天早上 06:23（新加坡时间）**：自动抓取过去 3 天的新论文，加入当周这一期（以本周一的日期命名，如 `data/issues/2026-11-09.json`），直接发布。
   - 已经在任何一期出现过、或被你删除过的论文不会重复出现
   - 周一如果还没抓到新论文，网站继续显示上一期，不会出现空的一期
2. **你随时可以修改**：在 GitHub 网页上打开当期的 `data/issues/<日期>.json`，点铅笔编辑，保存（Commit）后网站自动更新：
   - 删掉不相关的文章，并把它的 `id` 加进 `"removed"`，以后不会再被抓回来
   - 选 **Feature of the Week**：`"feature": true`，在 `"why"` 写一句为什么重要
   - **Editor's Picks**：`"pick": true`
   - 改 `"summary"`、`"categories"`、`"institutions"`
3. 侧栏 Archive 保留所有往期。

**手动工具**：*Actions → Manual run (draft PR) → Run workflow*，可以补抓某段时间，或给某一期补缩略图（勾选 `refresh_figures`），结果以 PR 形式给你审核。

## Feature Articles 与 Feature of the Week

- 每期（周一到周日）选 10–15 篇 **Feature Articles**：技术层面的创新（新模态、新仪器、灵敏度/速度/分辨率突破、新探针、新算法），或特别有意义的应用创新。每篇附 1–2 句 “Why it matters”。
- 每期一篇 **Feature of the Week**，从 Feature Articles 里选，置顶显示。
- 上限 15 篇：新入选使总数超过 15 时，按入选日期先替换最早的；Feature of the Week 不会被替换。
- 每天早上抓取完成后，定时任务会启动一个 Claude 会话：读当天新增的论文 → 选稿、写推荐语 → `python scraper/curate.py apply` → 推送到 main，网站自动更新。
- 手动调整：`python scraper/curate.py status` 查看本期入选；把 JSON 写成 `{"picks":[{"id":…,"why":…}], "feature":{"id":…}, "unpick":[…]}` 再 `apply`。也可以直接改 issue 文件里的 `pick` / `feature` / `why` 字段。

## 投稿 / 手动加文章

把 DOI 一行一个写进 `data/inbox.txt`，下次抓取时会被加入当期（不受关键词过滤限制），处理完会自动从 inbox 删除。

## 缩略图规则

- **开放获取（OA）文章**：用文章页自带的预览图（`og:image` / `twitter:image`），通常是 Fig. 1 或图形摘要，图下注明许可（如 `CC BY 4.0 · via nature.com`）。
- **非 OA 文章**：只用期刊封面，不使用论文配图。封面来源依次为：`config.yaml` 里 `journal_covers` 手动指定 → 文章页预览图本身就是封面（竖版或文件名含 cover）→ 通过 OpenAlex 找到期刊主页，取主页的封面图。封面按期刊缓存在 `covers/`，同一期刊只抓一次。
- **都没有**：网站显示技术类型小图标。

OA 判定：Europe PMC / Crossref 的许可证信息，再用 OpenAlex 复核（gold / diamond / hybrid 且为 CC 许可才算 OA）。
如果想像 OCT News 那样所有文章都用预览图，把 `figures.mode` 改成 `all`。

**给已有期次补图**：*Actions → Manual run (draft PR) → Run workflow*，填 `issue_date`（如 2026-11-02），勾选 `refresh_figures`，会开一个只更新缩略图的 PR。本地等价命令：`python scraper/vibranews_scraper.py --refresh-figures --issue-date 2026-11-02`。

抓取遵守 `robots.txt`，每个站点请求间隔 1 秒。

## 抓取范围与防漏

除振动光谱外，还收录三类相关主题（`config.yaml` 的 `extra_topics`）：**光声光谱**（PAS、QEPAS、LITES、中红外/光谱光声）、**光谱成像**（高光谱、多光谱、化学成像、快照光谱成像；卫星/航空/无人机遥感除外）、**新型光谱仪设计**（计算/重构型、片上、超表面、微型光谱仪等；质谱、NMR、X 射线等除外）。标题命中即收录，摘要里出现一次专门术语也收录；高光谱只在标题命中时收录。

**去杂（`config.yaml`）**
- 期刊质量：只收主流出版社（ACS、RSC、Elsevier、Springer Nature、Wiley、Optica、AIP、APS、IOP、IEEE、SPIE、T&F、SAGE、MDPI、Frontiers 等，见 `venues.trusted_doi_prefixes`）和 arXiv / bioRxiv / medRxiv / ChemRxiv；Research Square、Preprints.org、CyberLeninka、会议合集等直接排除；其他期刊在 OpenAlex 的 h-index ≥ 40 才收（结果缓存在 `data/venues.json`）。核心期刊和 inbox 投稿不受影响。
- 非光谱含义：Raman 激光/放大器、STIRAP、原子光学、Raman 激光雷达、分布式测温、Orbach 弛豫、等离子体、微波光子 SBS 等（`exclude`）。
- 标题排除：顺势疗法、植物提取物成分分析、“合成与表征”、教育类等（`exclude_title`）。
- NIR/SWIR 的非光谱用途：组织血氧、量子点/光电探测器件等，除非标题里写明振动光谱技术（`soft_exclude`）。


不按期刊名单抓，而是按关键词检索四个数据源，任何期刊发表的相关论文都会进来：

| 数据源 | 覆盖 | 时间窗口（每日模式） |
|---|---|---|
| Europe PMC | PubMed、PMC、bioRxiv 等预印本；标题、摘要、关键词 | 过去 3 天**入库**的记录（晚入库的论文也能抓到） |
| OpenAlex | 几乎所有期刊；带摘要和作者机构 | 过去 30 天发表的论文（重复自动跳过） |
| Crossref | 所有注册 DOI 的期刊 | 过去 3 天注册的 DOI |
| arXiv | physics.optics / chem-ph / bio-ph 等 | 过去 3 天提交 |

防漏规则：
- 各数据源的记录先按 DOI 合并、再判断相关性，没有摘要的记录会借用其它来源的摘要
- 标题命中关键词即收录；SERS、SRS、CARS、布里渊显微、O-PTIR、ROA/VCD 等专门技术名在摘要里出现一次即收录；泛泛的 "Raman" 需要标题命中或摘要出现两次（避免 "XRD、Raman、XPS 表征" 这类材料论文刷屏）
- 核心期刊 **Journal of Raman Spectroscopy、Vibrational Spectroscopy、Journal of Near Infrared Spectroscopy** 的每一篇都收录，不看关键词（`core_journals`）
- 已在任何一期出现过、或被删除过的论文不会重复
- 发表超过 180 天的旧论文忽略（重新索引的旧文、迟到的勘误）
- 每次运行的各数据源数量、去重后数量、相关数量、新增数量记录在 `data/stats.json`
- 发现漏掉的论文：把 DOI 写进 `data/inbox.txt`，第二天就会收录

## 调整抓取范围

全部在 `scraper/config.yaml`：

- `include` / `exclude`：关键词正则。标题命中一次，或摘要命中两次，才算相关；`exclude` 用来排除 fNIRS、拉曼光纤放大器等
- `europepmc_terms`、`crossref_queries`、`arxiv_categories`：各数据源的检索词
- `categories`：分类规则，分组需与网站一致（Applications / Technology / Business News / Miscellaneous）
- `summary_max_chars`：网站上摘要截断长度

## 本地运行

```bash
pip install -r scraper/requirements.txt pytest
python -m pytest -q scraper/tests                         # 离线自测
python scraper/vibranews_scraper.py --daily                 # 每日模式：过去 3 天的论文写入本周一期
python scraper/vibranews_scraper.py --issue-date 2026-11-09 # 按周模式：某一期补抓上一周
python -m http.server 8000                                # 浏览 http://localhost:8000
```

常用参数：`--since/--until` 指定时间窗，`--no-figures` 跳过下载缩略图，`--dry-run` 只打印结果不写文件。

## 目录

```
index.html                 网站（一页，读取 data/）
data/issues/index.json     期次列表
data/issues/<日期>.json     每一期的文章
data/meetings.json         会议列表（手动维护）
data/inbox.txt             投稿 DOI
figs/                      文章缩略图（自动生成）
covers/                    期刊封面缓存（自动生成）
scraper/                   抓取脚本、配置、测试
.github/workflows/         每日抓取并发布、手动补抓/补图、推送后自动发布
```
