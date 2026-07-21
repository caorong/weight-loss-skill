---
name: diet-profile-card
version: 1.0.0
description: "Send the user's 减脂档案 (diet profile) as a mini-program card when the user asks to see their overall progress, data, or trajectory. Trigger phrases: '减脂档案', '我的档案', '看看我的档案', '看我的数据', '我的数据', '减脂曲线', '体重曲线', '我的进展', '看看进展', 'my profile', 'my progress', 'my data'. The card opens the mini-program dashboard. Do NOT trigger for '今天/今日' single-day queries (those go to personal-data-query) or for '周报/这周/weekly report' (those go to weekly-report)."
metadata:
  openclaw:
    emoji: "chart_with_upwards_trend"
---

# Diet Profile Card

> 🚨 **OUTPUT CONTRACT: 命中即发一张小程序卡片。卡片本身就是回复 —— 成功后回 `NO_REPLY`,不再补任何文字。发卡失败才回退到一条文字。**

> ⚠️ **SILENT OPERATION:** 不要向用户复述内部动作,直接发卡。

用户想看自己的"减脂档案/整体进展/数据/曲线"时,发一张减脂档案小程序卡片(固定底图 +「{Name}的减脂档案」标题,点击进小程序 dashboard)。这是**档案级/整体**视角,区别于当日查询(personal-data-query)和周级汇总(weekly-report)。

---

## 触发边界

**命中(发档案卡):**
- 明示:"减脂档案""我的档案""看看我的档案""看我的数据""我的数据"
- 曲线/趋势:"减脂曲线""体重曲线""我的进展""看看进展""最近怎么样"(泛指整体历程时)
- 英文:"my profile" / "my progress" / "my data"

**不归本 skill(交给别人):**
- ❌ 含"今天/今日/现在"的单日查询("今天吃了多少""今日进度")→ `personal-data-query`
- ❌ 含"周报/这周/上周/weekly report" → `weekly-report`
- ❌ 记录食物/体重 → `diet-tracking-analysis` / `weight-tracking`

拿不准是"当日"还是"整体"时:提到"档案/曲线/趋势/进展/一直/最近这段"偏整体 → 本 skill;明确"今天/现在" → personal-data-query。

---

## 执行流程

### Step 1: 写一句当下语境的短文案

发卡时可**附带一句贴合当下对话的短文案**,连着卡一起发。**不传文案则只发卡片、不发文字**(无任何兜底文案)——自然语言场景建议显式传一句,让回复更自然。

文案要求:1-2 句,口语、轻松,承接用户刚才的话,引导点开卡片看档案。用 `USER.md > Name` 称呼。示例:
- "来啦～你的减脂档案都在这张卡里,点开看看这段时间的变化 👇"
- "这段时间的数据我都给你整理进档案了,点卡片瞅一眼 👇"

语言按 `USER.md > Language`(zh/en),不要自行推断。

### Step 2: 发卡

```bash
bash {baseDir}/scripts/send-profile-card.sh "{workspaceDir}" "{Step2 的短文案}"
```

- 脚本从 `workspaceDir` 目录名读取 `account_id`,再转调业务统一入口 `shared/card/build-and-send-card.sh`(反查 robotId、取密钥、生成封面 SVG),底层由 backend `send-miniprogram-card.mjs` 渲染发送
- 输出 `ok` = 成功 → **本轮结束,回复 `NO_REPLY`,不再发任何文字**
- 非 0 退出 = 发送失败 → **仅此时**回退到一条文字(见下),保证用户有回应

**回退文案(仅发卡失败时):** 简短告知,不承诺链接(档案卡无独立 H5 URL,失败就纯文字安抚):
```
{Name}~ 你的减脂档案我这就整理,稍等我一下 🙌
```
