# m3 キャンペーン自動参加

GitHub Actions で毎日 m3.com にログインし、次の2つを自動で行います。

- **ログインボーナス**: ログインし、「ポイントを受け取る」などのボタンがあれば押す
- **エントリー型キャンペーン**: キャンペーンページを開いて「エントリーする」「応募する」などのボタンを押す（superm3の日の対象も含む）

動画視聴・記事閲覧・アンケート回答が条件のキャンペーンは**対象外**です。該当するボタン（「視聴」「回答」「アンケート」を含むもの）は押しません。

## セットアップ

1. リポジトリの **Settings → Secrets and variables → Actions** に次の2つを登録します。
   - `M3_LOGIN_ID`: m3.com のログインID
   - `M3_PASSWORD`: m3.com のパスワード
2. **Actions** タブで「m3 キャンペーン自動参加」を開き、**Run workflow** で `dry_run` にチェックを入れて実行します。
   押す予定のボタンが実行結果のサマリーに表示されるので、内容を確認します。
3. 問題なければ、以後は毎日 7:10 (JST) に自動実行されます。

## うまく動かないとき

m3.com の画面構成に合わせて `m3_campaign/config.yaml` を調整してください。

- ログインに失敗する → `login.url` と入力欄のセレクタ
- キャンペーンページが見つからない → `campaigns.urls` に superm3の日の特設ページなどの URL を直接追加するか、`discover.link_pattern` を変更
- ボタンが押されない / 押してほしくないボタンが押される → `button_texts` / `exclude_texts`

ローカルでブラウザを表示しながら確認する場合:

```sh
pip install -r requirements.txt
python -m playwright install chromium
M3_LOGIN_ID=... M3_PASSWORD=... python -m m3_campaign.runner --headed --dry-run
```

ローカル実行では、最後に開いていたページのスクリーンショットが `artifacts/last_page.png` に保存されます。
このリポジトリは public のため、Actions ではスクリーンショットをアップロードしていません（ログやサマリーは誰でも閲覧できるので、個人情報は出力しないようにしています）。

## テスト

m3.com を模したローカルサイトで、ログイン → ボーナス受け取り → エントリーの流れを確認します。

```sh
python -m pytest -q tests
```

## 注意

- 自動操作は m3.com の利用規約で制限されている可能性があります。ご自身の判断で利用してください。
- アクセスは1日1回、操作ごとに間隔を空けています（`wait_ms`）。
