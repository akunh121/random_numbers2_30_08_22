# Emby Series Exporter

סקריפט פייתון שמייצא קישורי הורדה לפרקים של כל הסדרות תחת קטגוריה ב-Emby
לקובצי JSON (קובץ אחד לכל סדרה).

## התקנה

```bash
pip install -r requirements.txt
```

## הגדרה

העתק את `token.example.json` ל-`token.json` ועדכן את הערכים:

```bash
cp token.example.json token.json
```

```json
{
  "base_url": "https://play.embyil.tv:443",
  "username": "oren121",
  "token": "YOUR-EMBY-TOKEN",
  "parent_id": "1070346",
  "out_dir": "/storage/emulated/0/Download/EmbySeries"
}
```

## הרצה

עם token קיים:

```bash
python fetch_series.py
```

עם שם משתמש וסיסמה (משיג token חדש ושומר אותו ל-`token.json`):

```bash
python fetch_series.py --username oren121 --password 'SECRET' --save-token
```

ParentId אחר / תיקיית פלט אחרת:

```bash
python fetch_series.py --parent-id 1070346 --out-dir ~/EmbySeries
```

דילוג על סדרות שכבר נשמרו (להמשך לאחר ניתוק):

```bash
python fetch_series.py --skip-existing
```

## דגלי CLI

| דגל              | תיאור                                          |
|------------------|------------------------------------------------|
| `--config`       | נתיב לקובץ הגדרות (ברירת מחדל: `token.json`)   |
| `--base-url`     | כתובת שרת Emby                                 |
| `--token`        | X-Emby-Token                                   |
| `--username`     | שם משתמש להתחברות                              |
| `--password`     | סיסמה להתחברות                                 |
| `--parent-id`    | ParentId של קטגוריית הסדרות                    |
| `--out-dir`      | תיקיית פלט                                     |
| `--skip-existing`| דילוג על קבצי JSON קיימים                      |
| `--save-token`   | שמירת ה-token לקובץ ההגדרות אחרי לוגין מוצלח   |

## מה השתפר לעומת הסקריפט המקורי

- ארגומנטים מה-CLI עם `argparse` במקום ערכים קשיחים בקוד
- תמיכה בלוגין username/password ושמירת token אוטומטית
- `requests.Session` עם retries אוטומטיים ו-timeout לכל בקשה
- ניקוי שמות קבצים חוצה-פלטפורמות (`<>:"/\|?*`)
- אפשרות לדלג על סדרות שכבר נשמרו
- שגיאות מטופלות עם הודעות ברורות וקודי יציאה
- קוד מחולק לפונקציות (קל לבדיקה ולשינוי)
