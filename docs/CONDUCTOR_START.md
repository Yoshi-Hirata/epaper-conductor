# ショー UI(Conductor)の起動手順

> **要約**
> - **機体が自動で行うこと**: 基板の監視 / USB が切れたら即座に再接続 / 待機中の
>   バスの自動修復(USB リセット)/ 間隔の空いたキューの 9 秒前の確認 /
>   親基板に 60 秒以上 STOP を切らさない(keep-away ―― 詰まったキューの連続でも
>   オートプレイが戻らない。Timeline の琥珀色 `… without a stop` は警告のみ)
> - **オペレーターが行うこと**: 電源は 12 V → Radxa の順 / **① Upload** のあと、
>   `ready` のタイルで **Recover bus** を 1 回押し、`bus was already clear` と出るのを
>   確認 / タイルに赤で `bus degraded (n stalls) — press Recover bus, or reboot the Radxa`
>   と出たら **Recover bus** / それでも直らなければ Radxa を再起動、または
>   マスター基板の USB を抜き差し
> - **ショー中にしてはいけないこと**: 機体の再起動 / DIP スイッチの変更 / USB の
>   抜き差し / ① Upload

ショー PC で動かす Web UI(http://localhost:8765)の起動・停止・困ったときの手順。
UI 本体の使い方は README の「Conductor」の節を参照。

## 1. 起動する(通常)

1. リポジトリ直下の **`Start Conductor.bat`** をダブルクリックする
2. 黒いウィンドウ(タイトル `E-paper Show Conductor`)が開き、続いて既定のブラウザで
   http://localhost:8765 が開く
3. **黒いウィンドウは閉じない**。閉じると UI は止まる(開始済みのショーは各機体が自走するので
   止まらない)

- もう一度ダブルクリックしても二重には起動しない(ページが開くだけ)
- ブラウザを閉じてしまったら、http://localhost:8765 を開き直せばよい(UI は動いたまま)
- ブラウザのブックマークに http://localhost:8765 を入れておくと早い

デスクトップにショートカットを置きたいときは、`Start Conductor.bat` を右クリック →
「ショートカットの作成」→ できたショートカットをデスクトップへ。

### 1b. Conductor は 2 つある(本番と展示)

リポジトリ直下には起動用の bat が 2 つある。**同じプログラム**だが、**別のアプリ**として動く:

| | 本番ショー | 展示(EXHIBITION) |
|---|---|---|
| 起動 | `Start Conductor.bat` | `Start Exhibition Conductor.bat` |
| ページ | http://localhost:8765 | http://localhost:8766 |
| データ | `showdata/` | `exhibition-data/` |
| 黒いウィンドウのタイトル | `E-paper Show Conductor` | `E-paper Exhibition Conductor` |
| ページの見た目 | いつもどおり | 左上のアプリ名の隣に琥珀色の **EXHIBITION** バッジ、タブの題名が `EXHIBITION · Conductor`、タブのアイコンも琥珀色 |
| バックアップ | `Backup showdata.bat` | `Backup exhibition-data.bat` |

展示の Conductor で何をしても本番のショー(`showdata/`)は変わらない。逆も同じ。
**ただし機体は共通**なので、**こちらで Upload / START する前に、もう一方の黒いウィンドウを
閉じる(Ctrl+C)**。「もう一方のボタンを押さなければよい」では**足りない**: 開いているだけの
Conductor も機体に手を出す(自分が始めていないランに `stopped (missed STOP)` で STOP を送る、
予約済みの消去を実行する。両方が同じ機体を見ると互いの T0 も補正し合う)。
もう一方が動いていると、起動時の黒いウィンドウに `WARNING: another Conductor is running on
port …` と出て、ページの上部に赤い 1 行(`⚠ another Conductor is running on port 8766
(workspace exhibition-data) [EXHIBITION] - close its black window (Ctrl+C) before Upload or
START here …`)が出続ける ― もう一方を閉じれば数秒で消える。同じポートに別の Conductor
(別フォルダ・別ラベル)がいるときは起動そのものを断る(`a different Conductor is on port …
close it`)。PC の展示 Conductor はショーを作って radxa-05 に送るまでが役目で、会場では
radxa-05 自身の Conductor がショーを回す(`radxa/EXHIBITION.md` 4 章)。

### 1c. 国立駅の Conductor(第 3 のワークスペース、2026-11-22)

JR 国立駅の 2026-11-22 のショー用に、もう 1 つ別のアプリがある。同じプログラムで、1b と同じ
ルールが当てはまる(**こちらで Upload / START する前に、他の Conductor の黒いウィンドウを閉じる**)。

| | 国立駅(KUNITACHI) |
|---|---|
| 起動 | `Start Kunitachi Conductor.bat` |
| ページ | http://localhost:8767 |
| データ | `kunitachi-data/`(git 管理外) |
| バックアップ | `Backup kunitachi-data.bat` |

- 中身: PC の本番ショーの 7 着(同じ機体 radxa-01〜04・07・09・10、同じ絵・DIP 上書き・ラベル。
  2026-11-19 に `showdata/` からコピー)、音源 `music/1121_kunitachi_all_BGM.mp3`(39:55.92 = 2395.92 秒、
  57.9 MB、MP3 192 kbps。**2026-11-21 に差し替えて、これが現在の音源**。前の `kunitachi100_music_1120g.mp3`(36:33.94)の前に 55 秒 5 フレームが足され、77 キューすべてを +55.2 秒ずらした。バックアップは `music_prev/` と `show.json` / `history.json` の `*.bak-20261121-before-1121music`)、ショーの長さ 2396 秒。**Timeline を編集したら全着の Upload をやり直す**。各着の
  プリセット(q00)は 0:00 にある(運用者が動かした)。最初の変化は LOOK 23 8:57、24 9:12、28 9:27、27 9:42、26 9:57、25 10:12
- `fleet.json` は `showdata/` のコピー(radxa-09 は `10.42.0.109:8787` = AZ-Epaper ホットスポットの
  アドレス)。**会場のネットワークで機体に届くか確かめる**
- 設計者用シミュレーター(音源入り)は Timeline の **Simulator for designers…** ボタン
  (この Conductor が動いている間)か
  `python tools/build_designer.py --music auto --workspace kunitachi-data --out dist/az27ss-simulator-kunitachi-with-music.html`
  (77.9 MB。**Fit to music** でショーの長さが 39:56 になる。音源だけで、国立駅のキューは入らない)
- ショーは最長 40:00・音源 128 MB まで(変更なし。2396 秒は上限の内)。Conductor の Timeline にズームは無く(未マージ)、39:56 では 7 秒のキュー 1 つが
  数ピクセル幅。目盛りは 30:00 を過ぎると 2:00 ごと。1 機体 18 送信までの上限は変わらない
- **会場のネットワーク**は 4b 節。会場では機体の LCD の GIT PULL が使えない(更新は PC から送る)
- **START の前に全機が online であることを確かめる**(これが基本)。万一 1 台が答えないときの予備が
  4d 節のスイッチ。ビーコン(4c 節)とあわせて、ON にするかは 11-22 の前に決める(既定はどちらも OFF)
- **音源つきの通し**は 2026-11-21 に新音源で済み(7 台とも q00〜q10、最大の遅れ 6 ms)。
  機体のスピーカー(radxa-05 の mpg123)で音源の遠い位置へシークするのは**未試験**

### 1d. 国立: 朝に Upload、会場では START だけ(2026-11-20、fe19d8c)

会場の電波は衣装の中で弱い(4b 節「電波」)。Upload は絵を何枚も送るので、**ルーターの近くで朝のうちに済ませ、
会場では START だけ**にする。機体と基板は電源が落ちてもショーと絵を保つ(2026-11-20 に実機で確認。記録
[BEACON_HWCHECK_20261120.md](BEACON_HWCHECK_20261120.md) 43〜45 行)。国立駅の `kunitachi-data/fleet.json` には
`"keep_uploads_on_restart": true` が入れてある(仕様は SPECIFICATION.md 4.7)。**Kunitachi の Conductor を一度起動し直すまで
効かない**(新しいコードも同じ。起動し直したら黒いウィンドウを開いたままにする)。

**朝(ルーターの近くで)**

1. `Start Kunitachi Conductor.bat` で起動。Units タブで **7 着が online**
2. タイムラインを**確定する(以降は編集しない)**。**「Clear pictures after the show」もこの時点で決める**
   (あとで変えると、Conductor を起動し直したあとに Upload が要る)
3. **① Upload** → 7 着が「書き込み済み」になるまで待つ
4. (任意)START → STOP で確認する

**移動・着替え・バッテリー交換**

- 機体と基板の電源は自由に切ってよい。起動した機体は白(スタンバイ)。**電源が戻ってから約 40 秒で PC とつながる**

**会場**

1. PC を `R2.net` に入れる。**Wavlink は朝の場所にも持っていく**(朝の Upload にも `R2.net` が要る)
2. Conductor を起動し直していた(またはこれから起動する)場合は、corrections に **`radxa-NN: holds this show already (adopted
   after a restart of the conductor)` が 7 着ぶん**出るのを待つ。START が
   `Not confirmed since the Conductor restarted: radxa-06 (waiting for its first reply).` で断られたら、**その機体の最初の返事を待つ**。
   理由の終わりが `- Upload` の行(`holds no show` / `holds another show` / `'Clear pictures after the show' differs` /
   `pictures cleared` / `pictures not written`)は、その機体に Upload が要る。`- wait`(`still writing its pictures` /
   `being confirmed`)は待つだけ
3. START の下の 2 つのスイッチ(`Backup broadcast (beacon)` と `START without units that do not answer`)は**オフで始める**。
   リハーサルで応答が途切れる機体が出たら ON を検討(4c・4d 節)
4. **③ START**。**Upload はしない**

**注意**

- **ショーの途中で Conductor を起動し直した**ときは、そのショーが終わったら **STOP** する。引き継ぎは STOP のあとに行われる
- 朝のあとにタイムラインや「Clear pictures after the show」を変えると、`changed since` で START が断られる(Upload が要る)
- **1 LOOK だけの Upload は、まだ確認されていない衣装の引き継ぎを取り消す**(その衣装はその LOOK の Upload が要る)。**会場ではしない**
- `--adopt` の Conductor(展示)と、このオプションの無い PC は従来どおり
- Wi-Fi が切れた機体は 30 秒後に自分でつなぎ直す(LCD に `wifi: reconnect R2.net`)。戻らない機体は LCD の WIFI メニューで
  `R2.net` を選ぶか、再起動(4b 節)

## 2. 停止する

黒いウィンドウを閉じる(または そのウィンドウで Ctrl+C)。

## 3. 前提

- Windows に Python 3.9 以降(このショー PC には 3.10 と 3.14 が入っている)
- 追加のインストールは不要(標準ライブラリだけで動く)
- ネットワークは **不要**。`localhost` はこの PC の中だけで完結するので、Wi-Fi やルータが
  変わっても UI 自体は開ける。機体(radxa-01〜10)との通信だけがネットワークに依存する

## 4. 開けないとき

| 症状 | 原因と対処 |
|---|---|
| ブラウザが「接続できません」 | UI が起動していない。`Start Conductor.bat` をダブルクリック |
| ダブルクリックしても黒いウィンドウがすぐ消える | 起動に失敗している。ウィンドウが消える前のメッセージを見る。残るときは `pause` で止まるので読める。多いのは Python 未検出(`Python 3.9 or newer was not found`)と、ポート 8765 を別のものが使っている(`cannot listen on port 8765`) |
| 黒いウィンドウに `already running` と出る | すでに起動している。ページが開くだけで正常 |
| 黒いウィンドウに `a different Conductor is on port …` と出てすぐ終わる | 同じポートに別の Conductor(別のフォルダ・別のラベル、例: `--workspace` を変えて手で起動したもの)がいる。その黒いウィンドウを閉じてからやり直す |
| 黒いウィンドウに `WARNING: another Conductor is running on port …`、ページ上部に赤い 1 行 | もう一方の Conductor(本番 8765 / 展示 8766 ― 1b 節)が開いている。**そちらの黒いウィンドウを閉じてから** Upload / START する。閉じれば赤い行は数秒で消える |
| Units タブが全部 offline | UI は正常。PC が機体と同じネットワーク(専用ルータ)にいない。つなぎ直せば自動で online に戻る。Designs / Timeline の編集はオフラインのままでできる |
| PC を再起動した・スリープから戻った | UI は起動し直す(`Start Conductor.bat`)。データは消えていない |

コマンドラインから起動するときは、リポジトリのフォルダで:

```bash
python -m conductor serve --open
```

別のポートで動かす(2 つ目を試すとき):`python -m conductor serve --port 8767 --workspace <別フォルダ>`
(8766 は展示の Conductor が使う ― 1b 節。`--label <名前>` を付けるとページにその名前の
琥珀色バッジが出て、窓を取り違えない)

## 4b. ネットワーク

- 機体は固定 IP **`192.168.51.101〜110`**(radxa-NN = 51.(100+NN))、ゲートウェイ・DNS は
  `192.168.51.1`。**専用ルータの LAN は `192.168.51.1 / 255.255.255.0`** にし、DHCP の配布範囲は
  `101〜110` を避ける(例: `192.168.51.20〜99`)。PC は DHCP のままでよい
- `192.168.50.x` にしていない理由: 家庭用ルータや上流回線の既定と重なりやすく、トラベルルータは
  WAN 側と衝突すると LAN を勝手に別の番号に変える(2026-09-22 に発生)
- PC が別の Wi-Fi に切り替わっていたら、Wi-Fi 一覧からルータの SSID を選び直す
- Units タブが全部 offline のときは、まず PC のアドレスが `192.168.51.x` かを確認(`ipconfig`)

**会場(国立駅)のネットワーク(2026-11-20)**

- 会場のルーターは **Wavlink、SSID `R2.net`**(管理画面 192.168.30.1、LAN 192.168.30.x は DHCP、**2.4 GHz だけ**。下の「電波」)。
  **ルーターモード(「メッシュルータモード」、WAN = DHCP)** に切り替えてある。以前の Repeater / WISP では、上流の
  スマホのホットスポットが切れると `R2.net` そのものが消え、PC は 35〜50 秒で落ち、機体は ssid-not-found になった。
  ルーターモードでは WAN のケーブルを抜いて 130 秒、9 台すべてが 3 秒ごとに答え続けた。**ショーに WAN の接続は要らない**
- 無線の上流(スマホのホットスポットなど)が使えるのは Repeater モードだけ。インターネットが要るときは、
  ASUS のトラベルルーターの LAN ポートを Wavlink の **WAN ポート**につなぐ
- 機体は固定 `192.168.51.1NN`、プロファイル `R2.net`(優先度 20、既定ルートなし)。そのため **R2.net では GitHub に
  届かず、機体の LCD の `GIT PULL` は会場では使えない**。更新は PC から git bundle で送る(PM が行う)
- PC は第 2 アドレス `192.168.51.40` で機体に届く。2026-11-21 から `R2.net` への PC の経路は TP-Link の Wi-Fi 7 USB アダプター(Windows 名 `Wi-Fi 2`)で、第 2 アドレスもそこにある(内蔵 Intel の `Wi-Fi` から移した。同じアドレスは 2 つのアダプターに付かないので、管理者 PowerShell で先に `Wi-Fi` を `Remove-NetIPAddress`、次に `Wi-Fi 2` を `New-NetIPAddress`)。Intel の `Wi-Fi` は `R2.net` につながない(同じ SSID に 2 枚だと経路が曖昧)。`New-NetIPAddress` で `Wi-Fi 2` の DHCP が切れたので 192.168.30.x が無く、**ルーターの管理ページ 192.168.30.1 は `Wi-Fi 2` から開けない**(Intel を短く使う)。PC のインターネットはスマホの USB テザリング。PC の `R2.net` プロファイルは「手動で接続」(勝手に
  つながない)。PC の Wi-Fi が別のものに替わっていたら `R2.net` を選び直す
- ASUS トラベルルーターの SSID は `yoshihirock.net_dev` / `yoshihirock.net_5G_dev` に改名された。機体に保存してある
  プロファイル `yoshihirock.net_5G` は一致しなくなったので、**機体は会場で別のネットワークへ飛ばない**(ショーには好都合)。
  代わりに予備のネットワークも無い(足すなら `tools/add_unit_wifi.sh`、または SSID を元の名前に戻す)
- 国立駅で使う 9 台(radxa-01〜04、06〜10。radxa-05 は使わない)は 2026-11-20 から 1aa943d
  (ビーコンの受信側と UPDATE FW の scan-wait 修正)。同じ日の夕方(約 17:25)から **7a31749**(下の Wi-Fi 見張り)も入っている

**電波(2026-11-20、屋外で広い範囲を歩く国立向け)**

- **Wavlink は 2.4 GHz だけにした**(屋外・遠くまで届かせるため。チャンネルは自動 = 8、帯域 20/40 MHz 自動、
  送信出力 = 詳細設定 → 信号調整 = 「高い」で、すでに最大)。機体のアンテナは変えられない
- 実測(radxa-08): 裸の機体を屋外で歩いて通信は約 150 m まで(運用者)。機体のログでは 1 分で約 -70 dBm、リンク速度は
  1 Mb/s まで下がり、約 -80 dBm で切れた。ルーターから 5 m: 衣装の外 -61〜-69 dBm(中央値 -64、25 Mb/s)、
  衣装の中 -73〜-77 dBm(中央値 -74、8〜17 Mb/s、ping は 30 回に 1 回落ちた)、ノイズ -89 dBm
- 読み方: **衣装の中は外より約 10 dB 弱い**(自由空間の届く距離で約 1/3)。機体そのものも携帯・PC より 15〜20 dB 弱い。
  Upload は**ルーターの近くだけ**でやる
- 残る手: ① Wavlink を衣装の通り道が見通せる高い所に置き、アンテナを立てる ② 会場で空いているチャンネルを 1 / 6 / 11 から選んで固定し、
  帯域を 20 MHz にする ③ 機体を衣装の外側・高い所に付ける
- PC は、**Wavlink の LAN ポートに LAN ケーブルでつなぐのが最も確か**。USB の Wi-Fi アダプターは PC と Wavlink の間しか
  良くならず、衣装の届く範囲は変わらない(候補に TP-Link Archer TBE400UH などを見たが、購入の決定はしていない。
  WAVLINK AX5400 は販売ページに技適の表示が見つからなかった)
- **機体の Wi-Fi 見張り(7a31749、全 9 台)**: ネットワークが 30 秒なくなると、機体が自分で最後に乗っていたプロファイル
  (ホットスポットには戻らない)へ `nmcli con up` でつなぎ直す(失敗しても 30 秒おき)。LCD のログに
  `wifi: reconnect R2.net` → `wifi: on R2.net`(長く切れたときは最初の失敗と 5 回おき)。WIFI 画面は試行中
  `reconnecting R2.net - KEY2 back`。radxa-08 で `nmcli con down R2.net` が 33〜47 秒で戻ることを確かめた。
  理由: 圏の端で WPA のハンドシェイクが時間切れになると NetworkManager 1.42 が「パスワード違い」(`no-secrets`)と
  見なして自動接続をやめ、圏内に戻っても機体が離れたままになった。**わざと `nmcli con down` した機体も戻る** ―
  ネットワークから外したいときは無線を切る(radio off は何もしない)か、`/etc/default/epaper-ui` の `UI_ARGS` に
  `--no-wifi-watchdog`。LCD の WIFI メニューで R2.net を選ぶか、再起動、という手動の回復はそのまま使える

## 4c. ビーコン(予備の一方向ブロードキャスト、2026-11-20)

機体の Wi-Fi は「ルーターから機体には届くが、機体の返信がルーターに届かない」ことがある。HTTP の命令は返信も
要るので、そういう会場では START しても機体が応えない。ビーコンは返信を要らない**予備の経路**。

**いつ ON にするか**: 返信がルーターに届かない会場だけ。**普段は OFF**。Units タブの **THE SHOW** の欄、③ START の行の下の「If a unit's Wi-Fi is weak:」の行にある
**`Backup broadcast (beacon)`** にチェックを入れる。

- **やること**: START / HOLD / RESUME / SEEK / NEXT / STOP を、HTTP のほかに **UDP でも毎秒数回** 送る。
  HTTP も従来どおり送る(**置き換えでなく予備**)。機体は先に届いたほうに従い、もう一方で戻されない
- **やらないこと**: Upload・PRESET・STANDBY・Release・ファームウェアはビーコンでは送らない
  (HTTP のまま。Upload はルーターの近くで先に済ませる)
- **機体の LCD で従ったか見る**(ログの行。一部は 17 文字ほどで切れる):
  - 従った: `beacon run, starts in 8s`(カウントダウン中に入った)/ `beacon run, joined 31s`(途中参加)/
    `beacon hold` / `beacon stop`
  - 見送った: `beacon skip: other PC`(別の PC のビーコンを先に聞いている)/ `beacon skip: clock off`
    (時計が合わない。再起動した機体など)/ `beacon skip: no show`(その機体にそのショーが載っていない)/
    `beacon skip: own demo`(機体が自分のデモを回している)
  - 断られた: `beacon NG: ...`(理由つき。例: 書き込み中)
  - `beacon: STOP wins` = 同時に届いた HTTP の STOP を優先して、もう一度止めた
- **チェックボックスの横の表示**: `(on, idle)` = ON だが送るものが無い(止まっている)/ `(sending)` = 送信中 /
  `(no route - check the Wi-Fi)` = 送り先の経路が無い(PC を機体と同じショーの Wi-Fi につなぐ)/
  `N send errors, last HH:MM:SS` = 送信エラーの回数と最後の時刻 / `N units left out` = N 台は送っていない
  (**マウスを載せると理由**: 時計をまだ測っていない、ホスト名が同じ、PC がその Wi-Fi にいない、など)
- **ルール**:
  - ビーコンを ON にする Conductor は**ネットワークに 1 つだけ**(本番と展示を両方 ON にしない)
  - 切り替えは**止まっているときだけ**
  - ON は fleet.json の `"beacon"` に保存され、**再起動しても ON のまま**
  - **ショーの途中で Conductor を再起動しない**: 再起動のあとは、次の Upload までビーコンは機体を飛ばす
    (STOP のあとに Upload し直す)
  - 再起動した機体は、HTTP の `/status` の応答が通って **Conductor が機体の時計を 2 回続けて同じ値に測れる**まで
    ビーコンでは操縦できない(`beacon skip: clock off`)。機体は壁時計が正しければ**自分でラン(ショー)を
    ディスクから戻し**、正しい位置で再開する。HTTP が戻れば約 7 秒で同期する(実機 2026-11-20)
  - バッテリー交換はショーの合間に(途中で電源を切ると上のとおり)。**電源が落ちた衣装は、絵を確かめてから**ステージへ戻す
  - 機体の壁時計: 機体には RTC があるが、ショーの Wi-Fi には NTP が無い。2026-11-20 の朝は全機が同じ古い時刻で起動し、
    再起動した機体は `loaded` のままだった。機体が一度 NTP に届いた(約 12:34 JST)あとは、再起動しても正しい位置に戻った
  - 機体にランが届いた(再起動して戻った・途中参加)とき、**現在の絵がもう一度出て、2〜3 秒後に次のキューが続く**ことがある
    (描き直しは約 7.3 秒)。シリアルポートがまだ開いていない機体には、現在の絵が約 0.4 秒おきに **2 回**出ることがある
    (プレーヤーの遅れ基板の救済で、ビーコン固有ではない)
- **ハードウェア確認(2026-11-20、radxa-06 + 基板 3、2 台の行は radxa-08 + 基板 20)は済んだ**。通ったもの: 0:00 の絵が
  1 回だけ、ビーコンだけでの START / HOLD / RESUME / SEEK / NEXT / STOP、STOP の 60 秒の尾、STOP → すぐ START、
  HOLD 中の SEEK、実際の再起動、ショーの途中の Conductor 再起動、途中のスイッチ切り替え、Conductor が 2 つ
  (機体は先に聞いたほうに従い `beacon skip: other PC`、先のものが黙って 5 秒で乗り換え)、別のショー ID は断る、
  聞こえない機体のいる Loop(その機体に周回は始まらない)、LCD を人が読むこと(運用者の動画 11-20 14:29。
  `beacon run, starts in 5s` / `beacon hold` / `beacon run, joined 23s` / `beacon stop` が全文で出る)、電源を完全に
  落とす再起動(電源を 2 回抜いた)。**未了**: 聞こえない機体が複数・弱い電波(きれいな切断でなく)・フルの長さ(36:34)の
  ラン(ビーコン ON)・LCD の `beacon skip: ...` / `beacon NG: ...` の行(撮影していない)。
  記録は [BEACON_HWCHECK_20261120.md](BEACON_HWCHECK_20261120.md)(行 37〜42)
- **電源が落ちた衣装(実機 2026-11-20、行 41〜42、O2 の答え)**: 機体の時計は**電源断で保たれない**(約 2 時間 10 分遅れて
  起動した)。時計を失った機体は保存されたランを範囲外と見て `loaded` のまま**何も自分では再生しない**(電源断のあとに
  誤った絵が出ることは無かった)。ビーコンだけでは戻れない(時計が合わない。仕様)。**電源が戻って約 40 秒(起動に 35 秒)と、
  PC への Wi-Fi の経路が要る**: HTTP が戻って 2 秒で正しい位置になる。機体の Wi-Fi が PC に届かなければ、届くまでそのまま。
  ウォームの再起動は時計が保たれる(RTC に電源がある)ので、機体が自分でランを戻す。試していない: 保存された時計が
  たまたまショーの中に入る場合(同じショーの間に起動か時計の保存があり、そのあと電源が落ち、HTTP も無い)
- **LCD の見え方**: 最新のログ行がヒントの帯に隠れる不具合は ab674dd で直した(全 9 台は 15:12 から)。ログの時刻は UTC。
  radxa-06 の LCD には欠けた縦の列がある(ハードウェア)
- **11-22 はビーコンを OFF のまま**にする(上の未了が閉じ、リハーサルで本当に要ると分かった場合だけ ON)。
  ON にするなら: Conductor は 1 つだけ / 止まっているときだけ切り替える / ショーの途中で Conductor の再起動もバッテリー交換もしない /
  機体を再起動したら絵を確かめてから衣装をステージへ戻す。仕様は [BEACON.md](BEACON.md) 8〜9 章

## 4d. 答えない機体を外して START(予備のスイッチ、2026-11-20)

**基本は「START の前に全機が online であることを確かめる」**。それでも 1 台が「not answering」のままだと、PC の
Conductor は(force を付けても)**全着の START を断る**。このスイッチは、そのときの予備。

**いつ ON にするか**: 普段は **OFF**。ビーコン(4c 節)を ON にする場合と**あわせて決める**(外した機体はビーコンの
放送で動くため、ビーコンが OFF だと、その機体は答えが戻るまで止まったまま)。Units タブの **THE SHOW** の欄、③ START の行の下の「If a unit's Wi-Fi is weak:」の行にある
**`START without units that do not answer`** にチェックを入れる。fleet.json の `"start_without_silent": true` に
保存され、再起動しても保つ(true / false 以外の値は警告を出して OFF のまま)。

- **外される条件**(ON のとき、素の START だけ): その機体の問題が **「not answering」だけ** で、**最後に聞いたときは
  完全に準備できていた**(同じショー ID、絵の焼き込み済み、force の免除なし)。**少なくとも 1 着は答えていること**
- **従来どおり断る**: ほかの不具合(Upload が要る・別のショーを持っている・書き込み中・クリア済み・基板の失敗)/
  この Conductor が起動してから一度も聞いていない機体 / 誰も答えない
- **使わない場面**: `Preset before START`(0:00 からの START)/ Loop の再開(Loop は自前の 60 秒の猶予)/
  展示の Conductor(ON にできず断られる。そちらは元から答える機体だけで始める)
- **START の前に見える表示**(THE SHOW の下): `Not answering: radxa-03 - if it is still not answering and its other
  checks pass, START can go without it; it may follow the backup broadcast, and joins fully when it answers again, if it
  is still ready.`(ビーコン OFF のときの後半は `... it stays as it is until it answers again, and joins then if it
  is still ready (the backup broadcast is off).`)。チェックを入れても使えないときは理由の行:
  `START will refuse on radxa-03: not heard from since this Conductor started - it must answer once first.` /
  `... was not ready when it was last heard.` / `START will refuse: another garment is not ready.` /
  `START will refuse: no garment is answering.` / Preset の文
- **START のあとの表示**: START のログ `Started without radxa-03 (not answering).`、corrections の
  `START without radxa-03 (not answering)`、ランの間(HOLD 中も)`Without radxa-03 in this run (not answering) - ...`。
  その機体が戻れば行は消える
- **外された機体はどうなるか**: ビーコン ON = 放送で始まる(実機で 0.08 秒以内に同期)。ビーコン OFF = そのまま止まり、
  答えが戻って約 1 秒後に supervision が「started late」で入れる。**ランの「start anyway」(force)は引き継がない**:
  基板の失敗を持って戻った機体は、force をつけた再 START か救済の Upload まで外れたまま。その機体への HTTP の START は送らない
- **切り替えは、ランが生きていないときだけ**(ENDED の後は可)。HOLD 中・プリセットのカウントダウン中は不可:
  `Stop the show first (or let it reach its end) - this can't be changed during a show, a HOLD or the preset countdown.`。
  START を判断している瞬間も不可: `START is being decided - try again in a moment.`
- 外した機体が START の判断中に 2 回戻ると `A garment came back while START was deciding - press START again.`
  と出る。もう一度 START を押す

## 5. データの場所

すべて `showdata/` フォルダ(リポジトリ直下、Git の管理外):

- `showdata/files/` … 取り込んだ CSV(`LookNN_map.csv`、`LookNN_color_NAME_grid.csv`)
- `showdata/show.json` … タイムライン、遷移の設定、機体の割り当て、LOOK 番号と型番、基板番号の書き換え、手で指定した DIP ID(`dips`)、音楽ファイル名
- `showdata/music/` … アップロードしたショーの音楽ファイル。
  **音源を差し替えたら Timeline タブの Simulator for designers… で作り直して演出家に渡す**
- `showdata/history.json` … Undo / Redo の履歴

### CSV を取り込むとき(2026-09-27)

- 画面のどこにでも CSV を落とせる。**フォルダごと落としてもよい**(中まで辿る)。
  特定の衣装に入れたいときは Designs タブのその衣装の **Add CSV to this item**
- **既にあるファイルは絶対に上書きされない**。同じ名前で中身が違うデザインは
  `…_color_pattern01-2_grid.csv` のように番号が付いて両方残る。中身が同じなら
  「もう入っています」と出て何もしない
- **大文字・小文字は区別しない**。Windows が同じファイルとして扱うので、
  `…_Pattern_grid.csv` は `…_pattern_grid.csv` と同じ名前とみなす
  (番号が付く/「もう入っています」になる)。`.CSV` は `.csv` に直して保存する
  (機体側は `*.csv` しか見ないため)
- **配線(`*_map.csv`)だけは番号を振れない**(1 着に 1 枚)。差し替えるときは
  Designs タブでその `*_map.csv` を Delete してから入れ直す。
  他の衣装の配線を Add CSV to this item で入れようとすると断られる
- ファイル名に配色案名が入っていないとき(型番だけの `AZ271SD1305.csv` など)は
  断られる。`型番_配色案名.csv` の形で入れ直すこと
- ファイル名が `*_map.csv` / `*_color_NAME_grid.csv` / `*_HW.csv` のどれでも
  ないときは **1 行目を見て** map か配色かを判定し、正式な名前に直して保存する。
  どの衣装か分からないときだけ断る(そのときは衣装の Add CSV を使う)
- Mac が付ける `._NAME.csv`・`.DS_Store`・`__MACOSX/` と、Safari が付ける
  `.csv.txt` は自動で処理される
- 取り込みのあとのメッセージに**拾った全ファイルの行方**が出る
  (入った / 別名で入った / もう入っていた / 断った)

### DIP ID を手で指定するとき(2026-09-27)

Designs タブの **BOARDS AND DIP SWITCHES** で、**DIP ID の欄をクリックして
数字を入れる**(Enter か、欄の外をクリックで確定)。SWITCHES ON はその場で
付いて回る。手で入れた ID には **set by hand** の札が付き、その **×** で
ランクに戻る(欄を空にしても同じ)。Undo / Redo が効く。

- **既定は「番号の小さい順に 1, 2, 3…」**(ランク)。27 枚の衣装なら DIP 1〜27
- **使うのは「衣装側でスイッチを変えたとき」だけ**。例:AZ271SD1301(LOOK 25)の
  DIP 27 のボードを 28 に変えた(2026-09-27)。27 枚しかないので、ランクでは
  28 と言えない。手で入れると **そのボードだけ**がランクから外れ、
  機体のボード一覧は **1〜26 と 28**(27 は空き)になる ― 機体側はこの
  飛んだ一覧をそのまま扱う(`group_count` は一覧の最大値)
- **他の衣装と衝突する ID は断られる**(同じ機体に載る衣装をまとめて見る):
  `DIP 26 would be used twice on radxa-03 (boards 117, 118)`
- 入れられるのは **1〜60**(バスの上限)
- **DIP スイッチが 4 本以上 ON の ID は通信が不安定との報告**があり、
  その行に琥珀色で注記が出る(`4+ switches on — reported unreliable;
  set another ID by hand`)。同じ文が Timeline タブの警告一覧
  (Upload の手前)にも出る。**警告だけで、ショーは止まらない** ―
  各衣装のスイッチは既定どおりに設定済みなので、直す時間があるときだけ
  別の ID を入れる。1〜60 で該当するのは 15, 23, 27, 29, 30, 31, 39, 43,
  45, 46, 47, 51, 53, 54, 55, 57, 58, 59, 60
- 手で入れた ID は `showdata/show.json` の `dips` に入り、Save show… /
  Simulator for designers… の JSON にも載る。**バンドルが `dips` を
  持たないとき(空の `{}` も同じ)は、この PC のものがそのまま残る** ―
  スイッチはここに立っているボードの話で、届いたタイムラインの話では
  ないため。中身のある `dips` を持つバンドルだけが置き換える(`boards`
  と同じ規則)
- **基板番号(BOARD NO.)を書き換えると、手で入れた DIP ID もその
  ボードに付いて移る**(同じ 1 ステップ。Undo で両方戻る)
- 画面を使わずに入れるには(本番 PC で、`AZ271SD1301` のボード 118 を 28 に):

  ```
  curl -X POST http://127.0.0.1:8765/api/boards -H "Content-Type: application/json" ^
       -d "{\"item\":\"AZ271SD1301\",\"dips\":{\"118\":28}}"
  ```

  戻すときは `28` の代わりに `null`。

### 演出家の bundle を読むとき(Load bundle…、2026-09-27)

Timeline タブの **Load bundle…**(演出家のシミュレーターの Save project… で
書き出したファイル)は、配色とタイムラインを演出家のものに置き換える。ただし:

- **配線(`*_map.csv`)はこの PC のものが残る**。bundle には演出家が使っていた
  写しが入っているが、配線は配線ナビから再生成したこちらのものが正しいので、
  **bundle の map は書き込まれない**。中身が違っていたときは結果のメッセージに
  **琥珀色で 1 行**出る:
  `AZ271SD1307_map.csv: the workspace's wiring is kept (the bundle's copy differs)
  - regenerate from the wiring site if the garment changed`
  → その衣装が本当に変わったのなら、**配線ナビから map を出し直して**入れること
  (bundle を読み直しても配線は入らない)
- **意図して map を差し替えるときは手で**:Designs タブでその `*_map.csv` を
  **Delete** してから **Add CSV**(上の CSV 取り込みの規則どおり)
- **まだこの PC に無い衣装の map は、そのまま入る**(新しい LOOK は配線ごと届く)
- 配色(grid)は**同名なら上書きされる**(Undo では戻らない)。上書きした数と、
  残した配線の数(`1 wiring file kept`)が結果に出る
- 古いレイアウト向けに描かれた配色は、**残った配線**と突き合わせて
  「made for another layout」と名指しされる(Designs タブの CHECK)

### Timeline でデザインを置くとき(2026-09-27)

- トラックの空いているところを**クリック**すると、そこにその衣装の次のデザインが
  置かれる。ポインタを乗せると**置かれるキューが薄く見える**
- トラック行の右端の **「+」** を押すと、直前のキューが描き終わって 1 秒後に
  次のデザインが置かれる(EDIT CUE の **＋ Add next design after this cue** も同じ)。
  ショーの終わりを越えるときは Show length が伸び、そう言う
- キューのドラッグは **1 秒刻み**(Shift を押しながらで 5 秒)
- 0:00 のブロックは **PRESET ·** と表示される
- 時間の欄は `3:20` でも `3.20` でも入る(演出家のシミュレーターは mm.ss 表記)。
  Show length の上限は **40:00**(2026-11-19 に 15:00 から。下の段落)

### 詰まったキューと親基板の STOP(keep-away、2026-09-28)

親基板は**最後のブロードキャスト STOP から約 85 秒で工場のオートプレイに戻る**
(show フレームでは戻らない)。LOOK26 のようにキューが 11〜25 秒おきに続くと、
以前の機体は描き直しとキュー直前を避けて STOP を 1 つも出さず、最後のキューの
約 7 秒後に USB が切れるかバスが劣化し、最後の絵が描きかけで残った(5 回中 5 回)。
いまの機体は**親基板に 60 秒以上 STOP を切らさない**: 最後の STOP から 40 秒たったら、
**絵が描き終わって 0.5 秒後、次のキューの 1.5 秒以上前**の最初の瞬間に STOP を
1 つ送る(ログ `keep-away stop (40 s since the last stop, 5.4 s before 35)`)。
キューが詰まりすぎてその隙間が無いときは 1 度だけ
`keep-away: no room for a stop between these cues (cues 9.8 s apart)` と言い、
キューの 1.0 秒前までに入れる。これで守れるのは、キューの間隔が「絵の完成 +
1.5 秒」(8 秒の絵なら 9.5 秒)以上のとき ―― それより詰まった間には STOP を
置けない。ローカルのデモ(SOLID16 など)と長い Upload の焼き込みでも同じように
送る。オペレーターがすることは無い(止めるのは `--no-keep-away`、SPECIFICATION 4.6)。
Timeline は機体の STOP の規則をショーの上で再生していて、CUES の上に琥珀色で
`AZ271SC6302: 94 s without a stop (0:54–2:28) - cues too close for the unit to send
one` と出たら、その時間帯は機体が STOP を入れられない(親基板がオートプレイに戻り
うる)。エラーではないが、その区間のどこかのキューの間を「絵の完成 + 2 秒」
(8 秒の絵なら 10 秒)以上空ける。`one picture takes 62 s - no stop can be sent
while it draws` は、その 1 枚の絵(refresh の上書き + sweep)が長すぎるという意味。
本番の LOOK26 では出ない。

### ショーの長さと音源の終わり(2026-09-28)

ショーの長さは Timeline 上の **Show length**(`10:55` のように m:ss)で決める。上限は
**40:00**(音源は 128 MB まで)、超えると `A show is at most 40:00` と出て元に戻る。音源を読み込むと、その横の
**Fit to music** が押せるようになり、押すと Show length が**音源の長さ(秒で切り上げ)**
になる(10:54 の曲なら 10:55)。**押さない限りショーの長さは変わらない。** トラックの上には
音源が終わる位置に amber の破線と `music ends 10:54` が出て、その後ろは斜線(`silence`)。
ショーの方が短いと Show length の横に amber で `music continues 0:54 past the end of
the show` と出る ― **本番前にこの文が出ていないことを確かめる**(曲の途中でショーが
終わり、END で音が切れる)。THE SHOW の大時計の下にも `music ends 10:54` が出る。
長さを変えたら **Upload し直す**(機体は Upload したときの長さで END を決める)。

**別の PC でも同じショーを使うには `showdata/` ごとコピーする**(演出だけなら Timeline の Save show… / Load show… の JSON でも移せる。音楽は別途)。 バックアップもこのフォルダを
丸ごと取ればよい。UI を起動したまま `showdata/files/` に CSV を置いても、次の再描画で拾う。

### サンプル演出(6 ルック通し)

`docs/samples/az27ss_sample_show.json` は 2026-09-24 に組んだ 6 ルック通しのサンプル(LOOK 23〜28、
19 キュー、遷移 6 種、機体は radxa-01〜06 に仮割り当て)。Timeline の **Load show…** で読み込める
(CSV は `showdata/files/` にあるものを参照するので、`showdata/` ごと移した先で使う)。デザイン CSV
がまだ無いルックに仮のデザイン(`*_color_sampleA/B_grid.csv`)を作るには、UI を起動したまま
`python tools/make_sample_grids.py . http://127.0.0.1:8765`(map から生成し、check を通してから取り込む)。

### 基板を見分けるとき(BOARD INFO、2026-09-28)

基板には印字されたシリアルも型番も無く、**名乗れるのは機体の USB に挿さっている 1 枚だけ**
(485 の先の基板は見分けられない)。見分けたい基板の USB を機体に挿し、機体の LCD メニューで
**BOARD INFO**(メニューの最後の行)を KEY1 で開くと、`SERIAL 5CF26F473930` / `TYPE 3930
(differs from most: 324C)`(大半の基板は `TYPE 324C`。それ以外は琥珀色)/ FW の版
(`FW V1.4 16-color (FW_260923+)`)/ この機体での書き込み記録(`FLASHED FW_260923 09-28
18:00 here` か `no flash record here`)が出る。FW を読むあいだは FW VERSION と同じく機体の
待機(白)が止まるので、**ショーの前後に、PC が機体を動かしていないときに**使う(PC が動かして
いる間は FW を読まず `FW (not read while the PC is driving)`)。KEY2 でメニューに戻る。
Units タブのタイルにも、その機体の USB の基板(マスタ)が小さく `master 5CF26F473930` と
出て、`324C` 以外なら琥珀色で `MCU ID 3930`。2026-09-28 に `3930` の 2 枚は色の出方が
ほかと違った ―― 見つけたら、どの衣装のマスタかを控えておく。

## 5b. 別の PC へ移す(移植)

コードは GitHub、ショーのデータは `showdata/` フォルダ、の 2 つを持っていけばよい。

1. 新しい PC に **Python 3.9 以降** を入れる(https://www.python.org/ 、インストーラで
   「Add python.exe to PATH」にチェック)。追加パッケージは不要
2. **Git** を入れて(https://git-scm.com/)、置きたい場所で:

   ```bash
   git clone https://github.com/Yoshi-Hirata/epaper-conductor.git
   ```

   Git を入れたくないときは GitHub のページの **Code → Download ZIP** を展開してもよい
   (その場合、以後の更新も ZIP を取り直す)
3. 元の PC で **`Backup showdata.bat`** をダブルクリック → リポジトリの隣に
   `showdata-YYYYMMDD-HHMM.zip` ができる(CSV・タイムライン・機体割り当て・音楽・履歴のすべて)。
   これを新しい PC のリポジトリ直下に展開して `showdata/` フォルダにする
   (`showdata/files/…` という階層になっていること)
4. `Start Conductor.bat` をダブルクリック → http://localhost:8765 が開き、Designs / Timeline に
   同じ内容が出る。Units タブは専用ルータにつなげば online になる(機体のアドレスは
   `192.168.51.101〜110` 固定で、設定不要)
5. コードを最新にするときはリポジトリのフォルダで `git pull`(黒いウィンドウを閉じてから
   `Start Conductor.bat` をやり直す)。`showdata/` は Git の管理外なので `git pull` で消えない

- 機体のアドレスを変えたいときだけ `showdata/fleet.json` を作る:
  `{"units": {"radxa-01": "192.168.51.101:8787", ...}}`(書いた機体だけ上書き)
- 別 PC で開発・テストもするなら `pip install pytest` のうえ `python -m pytest -q`(約 3 分)
- **展示(PC もルータも無い会場)**: Conductor を radxa-05 で動かし、ワークスペースは
  PC の**展示 Conductor**(`Start Exhibition Conductor.bat`、8766、`exhibition-data/` ― 1b 節)の
  Units タブの **Send workspace to …** で送る。別 PC へ移すときは `Backup exhibition-data.bat`
  の zip を `exhibition-data/` に展開する。手順は `radxa/EXHIBITION.md`
  (Loop、radxa-05 のスピーカー、`serve --host 0.0.0.0 --speaker`)

## 6. ショー当日の順番(要点)

### 本番前の起動手順(電源の順番・2026-09-28)

**ここを間違えると、絵は全部出るのに全キューが約 0.36 秒遅れる。** 2026-09-28 の
リハーサル(LOOK23 / radxa-01)で実際に起きた ―― 衣装の 12 V が切れたまま機体が
立ち上がり(`no boards answering`、焼き込み 0/112、USB ポートを 3 回開き直した)、
そのあと基板に電源が入り、Upload は 112/112 成功、START ―― **START の約 80 秒後から
シリアル書き込みが毎回 225〜390 ms ブロックし**(タイルの `Fired late by` の行が
`+211.7 ms` + 琥珀色の `bus stalled 359 ms`)、q03〜q06 が遅れ、ショーが終わったあとも
ハートビートが数分間 359 ms 詰まり続けた。**10 分後、基板に電源を入れたまま機体を
再起動したら全キュー +1〜6 ms・詰まりゼロに戻り、以後そのまま。**
つまり **電源・USB の立ち上がりが荒かった親基板は、全フレームを約 360 ms 遅らせる
状態に入りうる。**

> **何で直るか(2026-09-28 LOOK28 / radxa-07 で確かめた)**: 詰まった親基板には
> 2 つの状態がある ―― フレームを約 0.36 秒遅れて**実行する**状態(LOOK23:
> 絵は全部出た、遅れて)と、受け取っても**何も実行せず、何にも答えない**状態
> (radxa-07: プリセットでどのパネルも動かなかった)。後者が直るのは
> **親基板の USB リセット**(0.3 秒)、**Radxa の再起動(reboot = USB の電源が
> 入り直す)**、**親基板の USB の挿し直し**のどれか。**機体の UI サービスの
> 再起動では直らない** ―― ポートを開き直して基板を探し直すだけで、この親基板は
> それに答えない(`no boards answering` が 100 秒続いた)。前日「再起動で直った」
> のはどれも Radxa の再起動だった。機体はいまこの状態を見つけると**自分で USB
> リセットをかける**(ログ `bus recovered by usb reset (358 → 2 ms, master
> answers)`)。タイルの **Recover bus** はそれを今すぐやらせるボタン。

順番は必ずこれで。**確認するのは機体のログではなく Units タブのタイル**で、
下に書いたのはタイルに実際に出る文字そのまま:

1. **先に衣装側の 12 V を全部入れる**(親基板・子基板が生きている状態にする)
2. **そのあとで Radxa に電源を入れる / 再起動する**(すでに動いている機体も
   **Radxa ごと再起動(reboot)する** ―― UI サービスの再起動では、荒く立ち
   上がった親基板は直らない)。そのタイルが次の 3 つになるまで待つ:
   - **`Boards (answering / expected)` の行が `16 / 16`**(その衣装の基板数。
     `2 / 16` のままなら 1. に戻る)
   - **タイル最下段のログ行が `panels online: 16/16`**(機体が基板を全部
     見つけた行。行にマウスを置くとそれまでのログも出る)。
     `no boards answering → usb reset → panels online: 16/16` も同じ意味で
     よい(親基板が劣化していたので機体が自分で USB をリセットした)。
     `no boards answering → usb reset → still no boards` なら 1. に戻る
   - **機体名の行の右端の丸いチップが `standby`、そのあと `ready`**(`standby` は白の
     スタンバイを描き終えた状態、`ready` は Conductor の指示待ち。
     `local` は機体が自分のメニューにいる状態なので、まだ Conductor のものに
     なっていない)
3. **タイルに赤で `no boards answering` と出ている機体に Upload してはいけない。**
   先に 1. に戻る(焼き込み 0/112 のまま 3 分を捨てることになる)
4. **衣装の基板に機体より後から電源を入れてしまったとき、またはタイルの
   `Fired late by` の行に赤で
   `bus degraded (n stalls) — press Recover bus, or reboot the Radxa` と出ている
   ときは、Upload の前に直す。** まず赤い印の横の **Recover bus** を押す(機体が
   親基板の USB をリセットし、直ったことを確かめてから
   `radxa-07: bus recovered by usb reset (…)` と答える)。
   `radxa-07: bus recovery failed — reboot the Radxa or re-plug the master's USB`
   と出たら、**その Radxa を再起動(reboot)する**か、**親基板の USB を
   挿し直す**。**UI サービスの再起動では直らない。** NOW → NEXT ボードのその衣装の行にも
   赤で `bus degraded` と出る。③ START の下と WRITE TO UNITS には
   **琥珀色(amber)**で 1 行
   `radxa-01: bus degraded — press Recover bus (or reboot the Radxa), then Upload`
   (2 台以上なら `radxa-01, radxa-02: bus degraded — press Recover bus (or reboot these Radxas), then Upload`)
   ―― **START は止めない**(詰まっていてもキューは出る、ただ遅れる)。
   赤い印の横に Recover bus が**出ていない**(チップが `standby` や `local` で、
   Conductor のワーカーがまだ機体のポートを持っていない ―― 6. を参照)ときは、
   その Radxa を再起動する
5. **① Upload** → **「THE SHOW」カードが `pictures written on 10 / 10 units`**
   になり、**全機体のタイルの `Pictures` 行が `written`** になったのを確認
6. **Upload のあと、ready のタイルで Recover bus を押す**(1 台 1 回。ボタンは
   ショーが走っていない間、**Conductor のワーカーが機体のポートを持っている**
   タイル ―― Upload した機体、チップが `ready` / `armed` / `fired` / `failed` ――
   の `Fired late by` の行に出る。機体の `/status` の `owned` がそれを言い、
   チップの名前では決めない。**`standby` のタイルには出ない**: 白の
   スタンバイは描き終えるとワーカーが終わり、ポートはもう誰のものでもない ――
   機体も `unit is in standby - Upload first, then recover` で断る。
   **`local`(機体が自分のメニューやデモにいる)のタイルにも出ない**: そこで
   押すと機体を乗っ取ってデモを止めてしまうので、機体も
   `unit is on its own menu - nothing to recover from here` で断る。
   `owned` を返さない古い機体ソフトのタイルにも出ない)。
   トーストは **`radxa-01: bus was already clear`** でなければならない。
   **健全な衣装なのに `bus recovered…` や `bus recovery failed…` と出たら**、
   その機体の判定は当てにならない(機体は最後に答えた基板、直前の掃引で
   答えた一番小さい・一番大きいアドレスの基板の順に問い、どれかが答えれば
   健全とするので、1 番が無い衣装や途中で基板が落ちた衣装でもふつうは
   `already clear` と出る)―― **その機体は `--precheck 0 --no-auto-recover` で
   起動する**(直前点検と自動復旧を止める。SPECIFICATION §4.5 の「前提」)。
   タイルに琥珀色で **`no usb reset on this unit`** と出ている機体は、劣化しても
   自分では直せない ―― そうなったら Radxa の再起動か USB の挿し直ししかない
7. **③ START**

- 琥珀色の `bus stalled n ms` が出るだけなら、基板が描画中で USB を
  取らなかった普通の事象(赤にはならない)。赤になるのは **3 つそろったとき
  だけ**:**回数が 5 回以上**、**最後の 1 回が 150 ms 以上**、**最後の 1 回が
  2 分以内**。2026-09-28 の詰まりは毎回 225〜390 ms で、基板が描画中に
  取らなかっただけの詰まりは 50〜60 ms ―― **50〜60 ms が何回たまっても赤には
  しない**
- 数字の読み方:**回数はワーカーが立ち上がってからの通算**で、**「何秒前」は
  最後の 1 回だけ**が持っている。だから「5 回以上 + 最後が 2 分以内」は
  「まだ続いている」という意味であって「2 分間に 5 回」ではない。回数は機体の
  ワーカーが立ち上がるたび 0 に戻るので、**再起動すれば消える**
  (消えなければ直っていない)
- ショー中は赤の文言に切り替わらない ―― 本番中にタイルの言葉が変わらない
  ようにしてあり、再起動できるのは待ち時間だけだから。**この PC に run が
  無くても、どれかの機体が「走っている」と言っている間は同じ**
  (この PC を本番中に再起動したときの数ポーリング分)

> **タイムラインを機体に書き込む道は 2 つある。** どちらも Timeline タブの
> **Write to units…**(Units タブの **④ Save on units** ・「PLAY WITHOUT THIS PC」
> カードのボタンでも同じ)から選ぶ:
>
> - **A = Upload for the show** … **ショー当日はこちら**。いま全部の絵を基板へ焼き込み、
>   タイムラインを機体へ渡す。以後この PC が START / HOLD / NEXT / STOP を出す
>   (= 下の ① Upload と同じ動作)
> - **B = Save on the units** … **PC なしで流したいときはこちら**(§7)。各機体自身の
>   メニューに名前つきで保存し、機体が KEY1 で再生する
>
> どちらもダイアログ上部の **WHICH LOOKs** のラジオで「All LOOKs」(既定)か LOOK 1 つを
> 選べる。1 つだけ選ぶとその LOOK の機体にしか書かない。**必要なのはその LOOK の機体が
> 割り当て済みであることだけ** ― 他の衣装が Designs タブで機体未割り当てでも、
> 他の衣装のキューに問題が残っていても、1 ルックの書き込みは通る。書かなかった分は
> ダイアログに `Not written: AZ271SB2303 … — no unit yet` と黄色で出る
> (**All LOOKs は従来どおり全部そろっていないと断る**。未割り当ての衣装があれば
> その型番を名指しして止まる)。**START(と ② Show preset)は
> タイムラインの全機体が同じ Upload を持っていることを要求し、揃っていなければ断る** -
> `radxa-04 is not on this upload - Upload for All LOOKs before the show`。
> 1 ルックの Upload は確認用、本番前には必ず All LOOKs でもう一度 Upload すること
> (承知の上で進めるなら確認ダイアログで先へ行ける ― **焼き込み失敗の確認とは別の質問**で、
> 片方に「はい」と答えてももう片方は素通りしない。そのときは機体ごとに違う
> タイムラインが同時に走る)。
>
> いまタイムラインが機体に入っているかは、Timeline の「THE LOOKS AT」バーと
> Units タブの「THE SHOW」見出しのチップ(`uploaded 10 / 10 · up to date` /
> `demo "PARIS SS26" 10 / 10 · changed since`)で分かる。

1. PC を専用ルータにつなぐ → `Start Conductor.bat`
2. Units タブで 10 台が online、時計精度(±ms)が出ていることを確認
3. **① Upload**(ショーを全機体へ配布 = 各キューの絵を基板のスロットへ焼き込む)→ Units タブの
   「THE SHOW」カードが **Pictures written on n / n units** になるまで待つ(**36 基板 × 10 キューで
   約 112 秒、18 キューなら約 195 秒 = 3 分半**。各機体タイルの **Pictures** 行に `writing n / N` の
   進み具合が出る)→ **② Show preset**(開始前の絵を出す)→ **③ START**
   - **③ START のカウントダウン**(2026-09-29): ③ の隣の **`Countdown before START [11] s`**。
     ③ START を押すと大きな時計・NOW → NEXT ボード・ステージモニターが
     **`START in 11 s` / `-0:11` … `-0:01`** と数え(10 秒で琥珀、3 秒で赤)、**0:00 で
     ショーと曲が始まる**。**既定 11 秒**、3〜60 秒(小数可)。**ショーと一緒に保存**
     (`show.json` の `start_countdown_s`。リロードしても、別の PC でも同じ。undo 可、
     ショーの書き出し / 読み込みにも入る)。**変えても Upload は不要**(ショーの id に
     入らない)。**使うのは 0:00 から始めるときだけ** ― 途中位置からの START
     (`START FROM 1:00`)は右の欄の秒数で、表示も従来どおり。カウントダウン中は
     **NEXT を押せない**(`The show has not started yet`)
   - その右の **`NEXT / MOVE take effect in [3] s`** は NEXT・シーク(MOVE)・Back to 0:00・
     GO・途中位置からの START 用(従来どおりこのブラウザのメモリだけ、リロードで 3 に戻る)
   - どちらの欄も全角数字可、0.1 秒単位。値を打ってすぐ ③ START を押しても打った値が使われる
   - **③ START の前に「Clear pictures after the show」(③ の隣)を入れるかどうか決める**
     (§6c)。入れておくと、ショーが終わった時点で本番の絵をスロットから消すので、
     **衣装の Radxa を抜いたあとの自動巡回で本番の絵が出ない**
   - 中身の変わっていないショーをもう一度 Upload しても **1 枚も書かない**(約 0.02 秒で
     `written` に戻る)。時間がかかるのは初回と、キューの挿入・削除で slot 番号がずれたとき
   - 絵の無い(応答しない)基板は 1 枚あたり約 1.5 秒の待ちを 1 回だけ払う。機体の
     ログに `14 boards absent (3-16) - skipped` と出る
4. 本番中は HOLD / RESUME / NEXT / STOP
   - **Pictures 行の読み方**(この行が `written` でない機体があると ② ③ は機体名つきで断る):

     | Pictures 行 | 意味 | 操作 |
     |---|---|---|
     | `writing 12/48 (8 s)` | 焼き込み中 | 待つ。force では越えられない |
     | `written` | 全部の絵がスロットに入っている | そのまま ② ③ へ |
     | `✗ not written (cancelled: 理由) — Upload again` | 焼き込みが最後まで行かなかった(焼き込み中の STOP、機体がポートを取られた、ポートが無い) | **① Upload をやり直す**。force では越えられない |
     | `✗ not written since it restarted — Upload again` | Upload のあとに機体が再起動した | **① Upload をやり直す**。force では越えられない |
     | `✗ 10 of 12 pictures not written on boards 1, 2, 3` | 生きている基板が書き込みを拒否した(または不在) | その基板抜きで進めるなら ② ③ の確認ダイアログで「anyway」(`force`)。直すなら ① Upload |
     | `✗ none of its 16 boards answered` | その衣装の基板が 1 枚も答えない(電源が入っていない・ケーブルが抜けている) | 直すなら電源・ケーブル。**その 1 台を置いて他の 9 台で始められる**: ② ③ のダイアログで「anyway」(`force`)- その衣装はいま映っているものを映したまま |

   - **焼き込み中の STOP、焼き込み中の機体再起動 → ① Upload をやり直し、全タイルが written に
     なるまで待つ**
   - ショーが動いている最中でも ① Upload は押せる(確認ダイアログが出る)。1 台だけ絵を失った
     ときの復帰手段で、すでに同じショーを持っている機体は 1 枚も書かない。押した機体は
     いったんショーから外れ、次の監視ポーリング(数秒)で自動的に戻る
   - 機体が「run refused: …」で断ったときは、その理由が **その機体のタイルにも**出る
     (Corrected automatically の行だけでなく)
   - **タイルの琥珀のしるし 2 つ**(2026-09-27 追加。どちらも「ショーは動くが、その機体が
     あるべき状態に無い」という意味。問題が無ければ**何も出ない**):

     | しるし | 出る行 | 意味 | 操作 |
     |---|---|---|---|
     | `unit list 1-22 (exploring, 17-22 absent) ≠ show 1-16` | Boards | その機体がこのショーの基板一覧で動いていない ― ショーに無い基板を探し続けている。2026-09-26 のリハで radxa-04 がこの状態で、絵は全部書けていたので Conductor からは何も見えなかった | **① Upload をやり直す**(一覧が渡る)。直らなければ機体を再起動 |
     | `restarted since Upload` | Pictures | Upload のあとにその機体が再起動している(絵は基板のフラッシュに残っているが、機体がメモリにだけ持っていたものは消えている) | Pictures 行が `written` のままなら進めてよい。怪しければ **① Upload をやり直す** |
   - **音源(「THE SHOW」カードの MUSIC 行)**:**③ START でこの PC が曲を鳴らす**。
     鳴り出す位置は**ショーの時計に合わせる**ので、`③ START FROM 1:00` のような途中開始でも
     曲の 1:00 から始まる。**HOLD で止まり、RESUME で続きから、STOP で止まって先頭に戻る。
     シークバーで動かせば曲も飛ぶ**(曲がショーより短ければ曲の終わりで止まり `track ended`)
   - `Music with THE SHOW` のチェックを外せば音は出ない(このブラウザに記憶される)。
     **Mute と Volume は Timeline のプレビューと共通** ― 音源は 1 つだけなので、ショー再生中に
     Timeline の ▶ Play を押すと `THE SHOW is playing the music` と出て断られる
   - **ショーの最中にページを開き直したときは、ブラウザが自分から音を出さない決まりなので、
     MUSIC 行に緑の「Music: click to join」が出る。1 回押せばいまのショーの位置から鳴り出す**
     (押すまで無音。ページはエラーにならない)
   - **音を出す Conductor は 1 台・1 タブだけにする**。同じ PC で 2 つのタブを開いた場合は
     片方だけが鳴り、もう片方は `another Conductor tab is playing it` と出て黙る。
     **別々の PC で 2 台開くと両方鳴って 0.1〜0.3 秒ずれる**(互いを知る手段が無い) ―
     音を出さない方は `Music with THE SHOW` のチェックを外すこと
   - 右の表示が今の状態:

     | 表示 | 意味 |
     |---|---|
     | `playing 1:23` | その位置を再生中(`· muted` が付いていれば消音中) |
     | `paused` | HOLD 中、または START の待ち時間(0:00 前)。RESUME/開始で続く |
     | `stopped` | ショーが走っていない、または終わった |
     | `track ended` | 曲がショーより短く、曲の終わりまで来た |
     | `blocked — click to join` | ブラウザが自動再生を断った。緑のボタンを 1 回押す |
     | `no track` | 音源が未アップロード。Timeline タブの **Load music** で入れる |
     | `off` | `Music with THE SHOW` のチェックが外れている |
     | `another Conductor tab is playing it` | 同じ PC の別タブが鳴らしている |

5. **「Clear pictures after the show」を入れて走らせたときは、STOP から 30 秒後に消去が
   始まる。全機体のタイルが `pictures cleared` になるのを待ってから Radxa を抜く**(§6c)。
   抜いたあとその衣装が自動巡回を始めても、映るのは白だけ。
   **途中で止めただけなら、その 30 秒の間に ③ START を押せば消去は取り消される**
   (大時計の下に残り秒数が出る)。**機体の KEY2 を押したり再起動したりすると
   待機の白を描いてしまう** ― 最後のルックを残したいなら基板の電源を先に切る
6. 終わったら黒いウィンドウを閉じる。機体は本体のメニューに戻る(Units タブの Release)
7. **PC と機体のコードは必ず一緒に更新する**。古いページは機体の `cancelled` / `none` を
   `written` のように見せてしまう(焼き込みの状態は 2026-09-25 に増えた)。`git pull` は
   ショー PC と 10 台すべてに

## 6b. NOW → NEXT ボード(大時計の下)とステージモニター

Units タブの **THE SHOW** カード、大時計のすぐ下。**次に何が、いつ、どの衣装で起きるか**を
1 行で言い、その下に**衣装ごとの行**を並べる。数字はすべて既存のもの(このワークスペースの
タイムラインと、2 秒ごとの機体ポーリング)から作っていて、新しい通信は増えていない。

**ヘッダー(1 行)**

- `NEXT 2:45 −12 s LOOK 26 Tops · LOOK 26 Skirt` … 次のキューの時刻、残り秒、変わる衣装。
  同じ瞬間に出るキューは 1 つにまとめて数える
- 右側に**その次の 3 つ**(時刻 · LOOK · デザイン名)
- **色**: 11 秒以上は普通、**10 秒以下で橙**、**3 秒以下で赤(点滅)**。送信の瞬間に
  **`FIRED` が 1 秒だけ光る**(オペレーター決定 2026-09-27:「10 s / 3 s はこの値でよい」)
- `NEXT ▶` を押している間は `NEXT in 3 s`(送信までの待ち時間)が出る
- 状態ごとの文言: START 前は `START runs from 0:00`(シークで開始位置を動かしていれば
  その時刻)、HOLD 中は `NEXT (HELD)` と `HELD at 6:23 — 12 s to the next cue after RESUME`、
  終わったら `SHOW ENDED`。シークバーを動かせば**その位置で計算し直す**
- **STOP のあと**は `START runs from 0:00 — the panels keep what they are showing until it
  does`。STOP はパネルの絵を消さないので、NOW 欄も
  `(whatever the panels kept)` と言い、縮小図は**出さない** ―
  いま何が映っているかはこのボードには分からないため(0:00 のプリセットに戻ったふりはしない)

**行(衣装ごと。全 LOOK を常に表示)**

| 列 | 中身 |
|---|---|
| LOOK | LOOK 番号 / 型番 / 機体名 |
| NOW | いま着ている絵の縮小図とデザイン名。**キューが飛んでいる間**はその絵が出来ていく様子と `changing… 5 s left` の進捗バー |
| ↓ | 遷移の向き(`↓` 上→下、`↑` 下→上、`◎` 図心から、`→` 左→右、`←` 右→左、`■` 一斉)と span 秒 |
| NEXT | 次の絵の縮小図・デザイン名・発火時刻・**その衣装だけの残り秒**。最後のキューのあとは `last design — until the show ends 2:00`、そもそもキューが 1 つも無い衣装は `no cue — nothing changes it` |
| 状態 | `Fired late by` と、機体自身の着弾確認(`re-sent` / `not applied`)- タイルと同じ言葉 |
| VITALS | 下記 |

- **バッグ(3 点)は折り畳み**。`▸ Bags (not synchronised)` を押すと開く。同期制御しないので
  普段は畳んでおく(オペレーター決定:「バッグのみ折り畳み(同期制御しないため)」)。
  **たたむ条件は「キューが 1 つも無いこと」が必須** ― そのうえで AZ271SG* / 型番に Bag /
  LOOK 番号なし、のどれか。**ショーが変える衣装は絶対に畳まない**(ファイル名が LookNN で
  なければ LOOK 番号は空のままだし、「Bag-strap Tops」は Bag を含むが上衣である)
- 機体が未割り当て・オフラインの行は灰色で `unit offline`

**VITALS(1 行)** ― `radxa-01 · lag +4 ±3 ms · rtt 6 ms · boards 12/12 · fired +8 ms`
(それぞれの値にマウスを乗せると意味が出る)

- **`lag` = この PC の時計と比べた遅れ**(**+ はその機体が遅れている**、− は進んでいる)。
  機体は自分の時計でショーを走らせるので、この PC が言う T0 と機体が持っている T0 の差が
  そのままズレになる(`conductor/fleet.py` の `show_lag_ms`)
- 続く **`±3 ms` はその機体の時計がどれだけ正確に分かっているか**(往復時間の半分)。
  **誤差の幅の中に収まっている lag は遅れではない**。タイルの `Clock accuracy` と同じ値
- START / シーク / RESUME / NEXT の直後は、**その機体が新しい T0 を受け取るまで空欄**に
  なる(ふつうは 2〜4 秒)。T0 はこの PC の中では即座に動くが、機体が答えてくるのは最大
  2 秒後で、その間の引き算は「30 秒シークしたから 30 秒ズレている」という無意味な数に
  なるため。**時間で待つのではなく機体の返事で判定する** ― 移動直後のポーリングは
  補正そのものを計算する元になるポーリングなので、時間だけで開けると古い 1 回を通してしまう
- `boards` は**その衣装の基板**のうち何枚が答えているか(1 台に上衣とスカートを載せている
  ときも、行ごとにその衣装の基板だけを数える)。`restarted 3 min ago` は
  10 分以内に立ち上がった機体にだけ出る(絵を失った機体の説明になる)
- **赤**: オフライン / `|lag| > 200 ms` / その衣装の基板が足りない。**橙**: `rtt > 100 ms`

**ステージモニター** ― `Stage monitor` ボタンで**同じボードを全画面・黒地**にする。袖の
モニターに出すためのもの。`ESC` かボタンで戻る(全画面が断られても黒い overlay として出る)。
**別のタブに移っても止まらない**(カウントダウンはそのまま動く)。音楽の
`Music: click to join` が必要なときはこの画面にも出るので、全画面のまま押せる。

## 6c. ショーのあと: 絵をスロットから消す(自動巡回対策・2026-09-27)

**起きたこと。** ショーのあと、STOP を押してから衣装の Radxa を抜いた(基板はまだバッテリーで
生きていた)。**約 1 分後に親基板が工場出荷時の自動巡回を再開し、スロット 0〜18 を順に映した ―
つまり本番の絵を勝手に再生した。** USB が抜けたあとは何をしても止められない。だから
**衣装を抜く時点で、絵がスロットに残っていてはいけない**。

**使い方。** Units タブ **THE SHOW** カードの **③ START の隣**にあるチェックボックス
**「Clear pictures after the show」**(既定は**オフ**)。

1. **START の前にチェックを入れる**(ショーごとに保存されるので、ページを開き直しても
   タブを変えても残る。undo で戻せる)
   - **できれば ① Upload の前に入れる。** この PC が終了時に消去を指示するので当日は
     どちらでも効くが、**機体自身にこの設定が入るのは次の ① Upload のとき**
     (絵は変わらないのでショーの id は変わらず、タイルは `✓` のまま)。
     ショーの途中で PC を失っても機体が自分で消すようにしたいなら、Upload の前に
     入れておくこと。チェックの隣にも同じことが出る
2. ショーを普通に走らせる。**HOLD では何も消えない**(続きがあるという意味だから)
3. **ショーが終わる**と、Conductor が各機体に消去を指示する。
   機体は**最後のキューの guard が過ぎるのを待ってから**、生きている全基板の
   **スロット 1〜18** を 1 つずつ削除する(スロット 0 の白と 19 の手動用は触らない。
   **全消去 0x15 は使わない**)
   - **時間で終わったとき**は、長さ + 5 秒で指示が出る。ただし
     **「再生して終わりまで来た」ときだけ**:**シークや NEXT で終わり(または終わりの
     1 秒前より後)へ飛んだときは消さない**。シークバーを動かした直後 30 秒は
     いつでも消さない ― 最後の数秒をシークで見ていたら絵が消える、を防ぐため
   - **終わり際で HOLD / RESUME / シークをすると、その run は「終わりへ飛んだ」扱いに
     なり、時間で終わっても消さなくなる。** そのときは大時計の下にこう出る:
     `This run was moved to its end rather than played there, so its own end will
     not clear the pictures — auto-clear off for this run, use STOP …`。
     消したいなら **STOP を押す**(30 秒待ってから消え、その間に取り消せる)
   - **STOP のときは 30 秒待ってから**指示が出る。STOP は「途中で止める」ときにも
     押すボタンで、消してしまうと ① Upload(約 3 分)をやり直すしかないから。
     **この 30 秒の間に ③ START(や ② Show preset・RESUME・NEXT・シーク・① Upload)を
     すれば消去は取り消される**。確認ダイアログと、大時計の下の一行に残り秒数が出る:
     `Pictures will be cleared on every unit in 21 s — press ③ START …`
4. 各機体タイルの **Pictures** 行を見る:

   | 表示 | 意味 | 操作 |
   |---|---|---|
   | `clearing 54/288`(琥珀) | 消している途中 | **待つ** |
   | `pictures cleared`(灰) | スロット 1〜18 が空になった | **抜いてよい** |
   | `12 slots not cleared (boards 3, 7)`(赤) | その基板のスロットにまだ絵が残っている | その基板は**電源を切ってから**抜く |
   | `cleared partially`(赤) | 途中で中断された(START を押した、機体がポートを失った) | もう一度消す(下の手動ボタン)か、電源を切ってから抜く |
   | `unit too old for clear …`(タイルの赤い行) | その機体のソフトが古く、この指示を知らない | その機体は**電源を切ってから**抜く。`git pull` で機体を更新する |

   大時計の下の NOW → NEXT ボードの見出しにも出る:
   `SHOW ENDED — clearing the pictures` → `SHOW ENDED — pictures cleared`。

5. **全機体のタイルが `pictures cleared` になってから Radxa を抜く。** 抜いたあとに
   その衣装が自動巡回を始めても、映るのは**白**(スロット 0)で、本番の絵は出ない

**消したあとは絵が無い。** START と ② Show preset は機体名つきで断る ―
`radxa-02: pictures were cleared after the last show - Upload again`。**force でも越えられない**
(基板に映すものが無いのだから)。もう一度走らせるなら **① Upload をやり直す**。

**衣装は消しても映ったまま。** この操作は**一切描き直さない**(`show slot N` も STANDBY の白も
送らない)。**Radxa がつながっている間、衣装は最後の絵を映し続ける。**

> **⚠ ショーのあと、機体の KEY2 を押さない・機体を再起動しない。** どちらも待機画面の
> **白**(スロット 0)を描いてしまい、衣装が白くなる。**最後のルックを残したまま片付けたい
> ときは、Radxa を抜く前に基板の電源を切る。**

**チェックを忘れた・1 台だけ消したい。** **Write to units…** ダイアログの下に
**「After the show — clear the pictures」→ Clear pictures now** がある。Upload と同じ
**WHICH LOOKs** の選択が効く。**ショーが走っている間・HOLD 中は押せない**
(`The show is running — press STOP first.`)。押すと何をするかを確認ダイアログが出す。

**チェックを入れただけでは Upload はやり直さなくてよい**(絵は変わらないので、ショーの id も
変わらない = 全タイルが `✗ old version` になったりしない)。Conductor が終了時に指示を出すため。
ただし**機体自身にもこの設定を持たせておきたい**(ショーの途中で PC を失っても機体が自分で
消すように)なら、次の ① Upload で一緒に渡る。

## 7. スタンドアローンでデモを流す(§6 の B)

展示会のブースなど、この PC を毎回持って行かなくても機体単体でショーの一部を
流したいとき。**ショー当日(A)は §6 の ① Upload の方**で、こちらではない。

1. **Write to units…** を押す(Timeline タブのツールバー、Units タブの
   **④ Save on units (plays without this PC)**、または「PLAY WITHOUT THIS PC
   (DEMO STORED ON THE UNITS)」カードのボタン。どれも同じダイアログ)
2. 右側の **Save on the units** に名前を入力(**A〜Z・0〜9・記号、最大 14 文字**。
   機体の画面は日本語を描けないので拒否される)。ループさせるなら「Loop」も。
   書けないときは理由がボタンの真上に出る(タイムラインの問題、**ショーが進行中**
   → 先に STOP、デモ再生中の機体がある、名前が空)
3. **Save on the units**(名前欄で Enter でも同じ)→ 機体ごとに ✓ written / FAILED が
   ダイアログに出る(オフラインの機体は FAILED。あとで online になってから改めて書き込む)。
   続けて機体側の手順も出る
4. 機体のジョイスティックでメニューを開き(**KEY2**)、STANDBY のすぐ下にできた行を選ぶ →
   **KEY1** で再生(**先に自分で絵を焼いてから**流れる。ダイアログに出ていた秒数ぶん待つ)。
   **KEY2** で停止してメニューへ戻る。**KEY1 長押し**で最初からやり直し。
   **スタンドアロンデモは再起動・停電後に自動で再開する。止めるのは KEY2 か
   PC の STOP。**(ショールームに置きっぱなしのループは、電源が戻れば
   そのまま続く)
5. 書き込んだデモは各機体タイルの「**On unit**」行と「Demos on the units」の表に並ぶ
   (機体・名前・キュー数・長さ・ループの有無)。要らなくなったら表の **Delete** で機体から消す
6. デモの再生中はその機体だけ PC の Upload・START・SEEK・RESUME・NEXT の対象から
   外れ、「FAILED — playing a demo - press STOP first」と出る。Units タブの
   その機体の「Show」行に `demo: <名前>` と出ていたら、まず **STOP** を押す
   (機体のデモも一緒に終わる)。それから改めて Upload・START すればよい

タイムラインを直したあとに再度 Write すると、同じ名前の場所へ上書きされる
(見出しのチップが `demo "PARIS SS26" 10 / 10 · changed since` と出ていたら、
機体のデモはいまのタイムラインより前のもの。
「Demos on the units」の **Timeline** 列が `older` と出ていたら、その機体の
デモはいまのタイムラインより前のもの — 上書きするまで古い内容のまま流れる。
比較する材料が無い場合(このセッションでまだ Upload しておらず、かつタイムラインに
問題が残っている場合など)は `older` ではなく「—」と出る)。
