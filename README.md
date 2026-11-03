# VibSpec News

振动光谱（Raman、SRS/CARS、SERS/TERS、IR/NIR、光热红外）社区周报网站，参照 OCT News 的形式：每周一自动抓取上一周发表的新论文，生成一期草稿，编辑审核后合并，网站自动更新。

- 网站：纯静态页面（`index.html`），托管在 GitHub Pages，读取 `data/issues/*.json`
- 抓取：`scraper/vibspec_scraper.py`，数据源为 Europe PMC、Crossref、arXiv（免费接口，不需要 API key）
- 自动化：GitHub Actions，每周一新加坡时间 07:17 运行，生成一个待审的 Pull Request

---

## 一次性设置（约 10 分钟）

1. **新建仓库**：在 GitHub 新建公开仓库，例如 `vibspecnews`，把本目录全部内容推上去（默认分支 `main`）。
2. **打开 Pages**：仓库 *Settings → Pages → Build and deployment → Source* 选 **GitHub Actions**。
3. **允许工作流写入和开 PR**：*Settings → Actions → General → Workflow permissions* 选 **Read and write permissions**，并勾选 **Allow GitHub Actions to create and approve pull requests**。
4. **填网址**：把 `scraper/config.yaml` 里的 `site.url` 改成你的 Pages 地址（如 `https://你的用户名.github.io/vibspecnews/`）。它只用于抓取时的 User-Agent，表明来源。
5. **第一次发布**：推送到 `main` 会自动运行 *Deploy site to GitHub Pages*，几分钟后网站上线，显示第 1 期。
6. **手动试跑抓取**：*Actions → Weekly issue (draft for review) → Run workflow*，留空即按本周一出刊；也可以填日期补抓。

## 每周流程

1. 周一早上，工作流抓取上周（周一至周日）的论文，开一个标题为 *VibSpec News No. N · 日期 — draft for review* 的 PR。
2. 在 PR 里编辑 `data/issues/<日期>.json`，手机网页版点文件右上角的铅笔即可：
   - 删掉不相关的文章，并把它的 `id` 加进 `"removed"`，以后不会再被抓回来
   - 选一篇 **Feature of the Week**：`"feature": true`，在 `"why"` 写一句为什么重要
   - **Editor's Picks**：`"pick": true`
   - 需要时改 `"summary"`、`"categories"`、`"institutions"`
3. 点 **Merge**，网站几分钟内更新。旧的期次保留在侧栏的 Archive 里。

重复运行同一期是安全的：已有文章和你的修改都会保留，只追加新发现的论文。

## 投稿 / 手动加文章

把 DOI 一行一个写进 `data/inbox.txt`，下次抓取时会被加入当期（不受关键词过滤限制），处理完会自动从 inbox 删除。

## 缩略图规则（同 OCT News）

1. 打开文章页面，读取它自带的预览图（`og:image` / `twitter:image`）。多数期刊给的是 Fig. 1 或图形摘要。
2. 如果预览图其实是期刊封面（文件名含 cover 等），或文章页没有预览图，就用期刊封面。封面会按期刊缓存在 `covers/`，也可以在 `config.yaml` 的 `journal_covers` 里手动指定。
3. 都没有时，网站画一个技术类型小图标（Raman / SRS / SERS / IR / MIP）。

每张图下面注明来源和许可（如 `CC BY 4.0 · via nature.com`）。抓取遵守 `robots.txt`，每个站点请求间隔 1 秒。

**版权提示**：开放获取（CC BY）文章的配图注明出处即可使用；非开放获取文章的预览图属于出版商。若想更保守，把 `config.yaml` 里 `figures.mode` 改成 `open_access_only`，非 OA 文章就只显示期刊封面或图标。

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
python scraper/vibspec_scraper.py --issue-date 2026-11-09 # 抓取并写入 data/
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
.github/workflows/         每周抓取、自动发布
```
