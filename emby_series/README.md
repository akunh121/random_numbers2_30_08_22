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

סדרה ספציפית לפי שם (חיפוש בכל הספרייה, בלי צורך ב-parent-id):

```bash
python fetch_series.py --series-name "הישרדות ישראל"
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
| `--series-name`  | חיפוש סדרה ספציפית לפי שם                     |
| `--out-dir`      | תיקיית פלט                                     |
| `--skip-existing`| דילוג על קבצי JSON קיימים                      |
| `--save-token`   | שמירת ה-token לקובץ ההגדרות אחרי לוגין מוצלח   |
| `--use-download-endpoint` | חזרה לאנדפוינט `/Items/{id}/Download` (דורש EnableContentDownloading) |

## איך הסקריפט משיג את הקישורים

ברירת המחדל היא לקבל את הקישור דרך **`POST /Items/{id}/PlaybackInfo`**.
השרת מחזיר `DirectStreamUrl` שמצביע ישירות לקובץ ה-mp4/mkv הגולמי —
זה מה ש-Emby Web/Android משתמשים בו לנגינה. הקישור הזה עובד גם
למשתמשים שמסומן עליהם `EnableContentDownloading=False`, כל עוד
`EnableMediaPlayback=True`.

אם בכל זאת תרצה את האנדפוינט הישן (URL סטטי בלי PlaybackInfo, מהיר יותר
אבל דורש הרשאת הורדה), הוסף `--use-download-endpoint`.

## אזהרות

1. הקישורים שמתקבלים מ-PlaybackInfo נושאים `api_key` חד-פעמי שתוקפו מוגבל
   (כמה שעות עד יממה). אם תפעיל את הסקריפט פעם נוספת, ייווצרו קישורים חדשים.
2. אם תוריד עם `wget`/`curl`/מנהל הורדות מאחורי Cloudflare, השתמש ב-User-Agent
   של דפדפן: `wget --user-agent="Mozilla/5.0" -i הסדרה.txt`.

## מה השתפר לעומת הסקריפט המקורי

- ארגומנטים מה-CLI עם `argparse` במקום ערכים קשיחים בקוד
- תמיכה בלוגין username/password ושמירת token אוטומטית
- מעבר ל-`cloudscraper` כדי לעבור הגנת Cloudflare (אופציונלי - יורד ל-requests אם לא מותקן)
- שימוש ב-PlaybackInfo במקום `/Download` (עובד גם בלי הרשאת הורדה)
- חיפוש סדרה ספציפית עם `--series-name`
- דדופ אוטומטי של עונות/פרקים שמופיעים כפול בספרייה
- ניקוי שמות קבצים חוצה-פלטפורמות (`<>:"/\|?*`)
- אפשרות לדלג על סדרות שכבר נשמרו
- שגיאות מטופלות עם הודעות ברורות וקודי יציאה
- קוד מחולק לפונקציות (קל לבדיקה ולשינוי)
