success = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<title>認証完了</title>
<style>
body {text-align: center}
</style>
</head>
<body>
<h1>認証完了</h1>
<h3>こんにちは {{name}} さん</h3>
<div>
<b>利用にあたっての注意を読んでない方</b>は<br>
<a href="https://github.com/Charahiro-tan/twitch-user-checker">こちら</a>から必ず読んでください
</div>
<br>
<div>
<b>登録解除したい方</b><br>
Twitchの設定→リンク→その他のリンクよりnew user checkerのリンクを解除してください
</div>
<br>
<div>
<b>お問い合わせ先</b><br>
<a href="https://twitter.com/__Charahiro">Twitter</a><br>
</div>
</body>
</html>
"""

error = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<title>エラー</title>
<style>
body {text-align: center}
</style>
</head>
<body>
<h1>エラー</h1>
<h3>エラーが発生しました</h3>
<h3>もう一度試してください</h3>
<div>
エラー: {{error}}
</div>
<br>
<br>
<div>
<b>なかなか成功しない方</b><br>
・必ず<a href="https://github.com/Charahiro-tan/twitch-user-checker">ここ</a>からアクセスしてください。URLは1回だけ有効です。<br>
・URLを踏んでから3分以内に許可してください。<br>
</div>
<br>
<div>
<b>お問い合わせ先</b><br>
<a href="https://twitter.com/__Charahiro">Twitter</a><br>
</div>
</body>
</html>
"""
