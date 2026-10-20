# Expense API

REST API לניהול הוצאות אישיות: Express + MongoDB (Mongoose) + JWT + Zod.
כל משתמש נרשם, מתחבר ומקבל JWT, ויכול לנהל רק את ההוצאות שלו.

## הרצה

```bash
cd expense-api
npm install
cp .env.example .env   # למלא DATABASE_URL ו-JWT_SECRET
npm start              # node --env-file=.env --watch app.js
```

| משתנה | תיאור |
|---|---|
| `PORT` | פורט השרת (ברירת מחדל 3000) |
| `DATABASE_URL` | כתובת MongoDB (Atlas או מקומי) |
| `JWT_SECRET` | סוד לחתימת הטוקנים |
| `JWT_EXPIRES_IN` | אופציונלי, תוקף הטוקן (ברירת מחדל `1d`) |

## מבנה

```
app.js
lib/connect.js
models/user.model.js, expense.model.js
routes/index.js, auth.routes.js, expense.routes.js
controllers/auth.controller.js, expense.controller.js
middlewares/auth.middleware.js, error.middleware.js, validate.middleware.js, objectId.middleware.js
validations/auth.schema.js, expense.schema.js
```

## Endpoints

| Method | Path | Auth | תיאור |
|---|---|---|---|
| POST | `/api/auth/register` | - | הרשמה (201 / 400 / 409) |
| POST | `/api/auth/login` | - | התחברות, מחזיר `token` (200 / 401) |
| POST | `/api/expenses` | Bearer | יצירת הוצאה (201) |
| GET | `/api/expenses` | Bearer | כל ההוצאות של המשתמש (200) |
| GET | `/api/expenses/:id` | Bearer | הוצאה לפי id (200 / 404) |
| PATCH | `/api/expenses/:id` | Bearer | עדכון חלקי (200 / 404) |
| DELETE | `/api/expenses/:id` | Bearer | מחיקה (204 / 404) |

בונוס: query params ב-`GET /api/expenses`:
`category`, `from`, `to` (תאריכים), `sortBy=date|amount`, `order=asc|desc`.
לדוגמה: `/api/expenses?category=food&from=2026-10-01&sortBy=amount&order=asc`

קטגוריות: `food`, `transport`, `shopping`, `bills`, `entertainment`, `other`.

## אבטחה

- סיסמאות נשמרות כ-hash של bcrypt (10 סבבים), `passwordHash` לא נשלח בשום תגובה.
- ה-`user` של הוצאה נלקח רק מה-JWT, ושדה `user` בגוף הבקשה מתעלמים ממנו.
- כל שאילתה על הוצאה מסננת לפי `_id` וגם `user`, אז הוצאה של משתמש אחר מחזירה 404 בלי מידע.
- הודעת התחברות כללית: "פרטי התחברות שגויים".
- ObjectId לא תקין מחזיר 400. שגיאות לא צפויות מחזירות 500 בלי stack trace.

## בדיקות Postman

`expense-api.postman_collection.json` כולל את 15 התרחישים מהצ'קליסט (ועוד בונוס).
מייבאים ל-Postman ומריצים לפי הסדר ב-Collection Runner על מסד נתונים נקי.
הטוקנים וה-`expenseId` נשמרים אוטומטית במשתני הקולקשן.
