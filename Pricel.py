import sqlite3
import logging
from datetime import datetime, timedelta
import random
import string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup # type: ignore
from telegram.ext import ( # type: ignore
    Updater,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
    ConversationHandler
)
import os
TOKEN = os.environ.get('TOKEN')
ADMIN_PASSWORD = '.....' 
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect('quiz_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        is_admin INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS questions (
        question_id INTEGER PRIMARY KEY AUTOINCREMENT,
        question_text TEXT,
        option1 TEXT,
        option2 TEXT,
        option3 TEXT,
        option4 TEXT,
        correct_option INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS game_sessions (
        session_id INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time DATETIME,
        end_time DATETIME,
        is_active INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_answers (
        answer_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        question_id INTEGER,
        session_id INTEGER,
        selected_option INTEGER,
        is_correct INTEGER,
        answer_time DATETIME,
        time_taken REAL,
        FOREIGN KEY (user_id) REFERENCES users(user_id),
        FOREIGN KEY (question_id) REFERENCES questions(question_id),
        FOREIGN KEY (session_id) REFERENCES game_sessions(session_id)
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scores (
        score_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        session_id INTEGER,
        total_score REAL,
        date DATE,
        FOREIGN KEY (user_id) REFERENCES users(user_id),
        FOREIGN KEY (session_id) REFERENCES game_sessions(session_id)
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS promocodes (
        promocode_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        code TEXT UNIQUE,
        date_issued DATE,
        is_used INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    ''')
    
    conn.commit()
    conn.close()

init_db()
QUESTION_TEXT, OPTION1, OPTION2, OPTION3, OPTION4, CORRECT_OPTION = range(6)

class QuizBot:
    def __init__(self):
        self.updater = Updater(TOKEN, use_context=True)
        self.dispatcher = self.updater.dispatcher
        
        self.dispatcher.add_handler(CommandHandler('start', self.start))
        self.dispatcher.add_handler(CommandHandler('admin', self.admin))
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('questions', self.questions)],
            states={
                QUESTION_TEXT: [MessageHandler(Filters.text & ~Filters.command, self.question_text)],
                OPTION1: [MessageHandler(Filters.text & ~Filters.command, self.option1)],
                OPTION2: [MessageHandler(Filters.text & ~Filters.command, self.option2)],
                OPTION3: [MessageHandler(Filters.text & ~Filters.command, self.option3)],
                OPTION4: [MessageHandler(Filters.text & ~Filters.command, self.option4)],
                CORRECT_OPTION: [MessageHandler(Filters.text & ~Filters.command, self.correct_option)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_question_creation)],
        )
        
        self.dispatcher.add_handler(conv_handler)
        self.dispatcher.add_handler(CommandHandler('startgame', self.start_game))
        self.dispatcher.add_handler(CommandHandler('stopgame', self.stop_game))
        self.dispatcher.add_handler(CommandHandler('close', self.close_day))
        self.dispatcher.add_handler(CommandHandler('leaderboard', self.show_leaderboard))
        
        self.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        self.dispatcher.add_handler(CallbackQueryHandler(self.handle_button))
        
        # Track question creation
        self.current_question_data = {}
        self.questions_created = 0
    
    def start(self, update: Update, context: CallbackContext):
        user = update.effective_user
        self._add_user(user)
        
        update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n\n"
            "Я бот для ежедневных викторин с призами!\n\n"
            "📌 Доступные команды:\n"
            "/start - начать работу\n"
            "/leaderboard - таблица лидеров\n\n"
            "Когда игра активна, просто отправь мне сообщение, чтобы начать викторину!"
        )
    def admin(self, update: Update, context: CallbackContext):
        if not context.args:
            update.message.reply_text("Используйте: /admin <пароль>")
            return
            
        password = context.args[0]
        if password == ADMIN_PASSWORD:
            user_id = update.effective_user.id
            self._set_admin(user_id, True)
            update.message.reply_text(
                "🔑 Вы авторизованы как администратор!\n\n"
                "🛠 Доступные команды:\n"
                "/questions - создать вопросы\n"
                "/startgame - начать игру\n"
                "/stopgame - закончить игру\n"
                "/close - завершить день и выдать промокоды"
            )
        else:
            update.message.reply_text("❌ Неверный пароль!")
    
    def questions(self, update: Update, context: CallbackContext):
        if not self._is_admin(update.effective_user.id):
            update.message.reply_text("❌ Только для администраторов!")
            return ConversationHandler.END
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM questions")
        count = cursor.fetchone()[0]
        conn.close()
        
        if count >= 5:
            update.message.reply_text(
                "В базе уже есть 5 вопросов. Хотите заменить их?\n"
                "Ответьте 'да' для подтверждения или 'нет' для отмены."
            )
            context.user_data['replace_questions'] = True
            return QUESTION_TEXT
        else:
            update.message.reply_text(
                "Создание нового вопроса. Введите текст вопроса:"
            )
            self.questions_created = 0
            return QUESTION_TEXT
    
    def question_text(self, update: Update, context: CallbackContext):
        if context.user_data.get('replace_questions'):
            if update.message.text.lower() == 'да':
                conn = sqlite3.connect('quiz_bot.db')
                cursor = conn.cursor()
                cursor.execute("DELETE FROM questions")
                conn.commit()
                conn.close()
                update.message.reply_text("Старые вопросы удалены. Введите текст нового вопроса:")
                del context.user_data['replace_questions']
                return QUESTION_TEXT
            else:
                update.message.reply_text("Создание вопросов отменено.")
                return ConversationHandler.END
        
        self.current_question_data = {'text': update.message.text}
        update.message.reply_text("Введите первый вариант ответа:")
        return OPTION1
    
    def option1(self, update: Update, context: CallbackContext):
        self.current_question_data['option1'] = update.message.text
        update.message.reply_text("Введите второй вариант ответа:")
        return OPTION2
    
    def option2(self, update: Update, context: CallbackContext):
        self.current_question_data['option2'] = update.message.text
        update.message.reply_text("Введите третий вариант ответа:")
        return OPTION3
    
    def option3(self, update: Update, context: CallbackContext):
        self.current_question_data['option3'] = update.message.text
        update.message.reply_text("Введите четвертый вариант ответа:")
        return OPTION4
    def option4(self, update: Update, context: CallbackContext):
        self.current_question_data['option4'] = update.message.text
        update.message.reply_text(
            "Введите номер правильного варианта (1-4):\n\n"
            f"1. {self.current_question_data['option1']}\n"
            f"2. {self.current_question_data['option2']}\n"
            f"3. {self.current_question_data['option3']}\n"
            f"4. {self.current_question_data['option4']}"
        )
        return CORRECT_OPTION
    
    def correct_option(self, update: Update, context: CallbackContext):
        try:
            correct_option = int(update.message.text)
            if correct_option < 1 or correct_option > 4:
                raise ValueError
        except ValueError:
            update.message.reply_text("Пожалуйста, введите число от 1 до 4:")
            return CORRECT_OPTION
        
        self.current_question_data['correct_option'] = correct_option
        
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO questions (question_text, option1, option2, option3, option4, correct_option) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                self.current_question_data['text'],
                self.current_question_data['option1'],
                self.current_question_data['option2'],
                self.current_question_data['option3'],
                self.current_question_data['option4'],
                self.current_question_data['correct_option']
            )
        )
        conn.commit()
        
        
        cursor.execute("SELECT COUNT(*) FROM questions")
        count = cursor.fetchone()[0]
        conn.close()
        
        self.questions_created += 1
        remaining = 5 - count
        
        if count < 5:
            update.message.reply_text(
                f"Вопрос сохранен! Осталось создать {remaining} вопросов.\n\n"
                "Введите текст следующего вопроса:"
            )
            return QUESTION_TEXT
        else:
            update.message.reply_text(
                "✅ Все 5 вопросов созданы! Викторина готова к запуску."
            )
            return ConversationHandler.END
    
    def cancel_question_creation(self, update: Update, context: CallbackContext):
        update.message.reply_text("Создание вопросов отменено.")
        return ConversationHandler.END
    
    def start_game(self, update: Update, context: CallbackContext):
        if not self._is_admin(update.effective_user.id):
            update.message.reply_text("❌ Только для администраторов!")
            return
            
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM questions")
        question_count = cursor.fetchone()[0]
        
        if question_count < 5:
            update.message.reply_text("❌ Недостаточно вопросов! Нужно 5.")
            conn.close()
            return
        cursor.execute("SELECT * FROM game_sessions WHERE is_active = 1")
        active_game = cursor.fetchone()
        
        if active_game:
            update.message.reply_text("⚠️ Игра уже активна!")
            conn.close()
            return
            
        start_time = datetime.now()
        cursor.execute(
            "INSERT INTO game_sessions (start_time, is_active) VALUES (?, ?)",
            (start_time, 1)
        )
        conn.commit()
        conn.close()
        
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🎉 Викторина началась! 🎉\n\n"
                 "Отправьте мне любое сообщение, чтобы начать играть. "
                 "У вас будет ограниченное время, чтобы ответить на все вопросы. "
                 "Чем быстрее вы отвечаете правильно, тем больше очков получаете!\n\n"
                 "Удачи! �"
        )
    
    def stop_game(self, update: Update, context: CallbackContext):
        if not self._is_admin(update.effective_user.id):
            update.message.reply_text("❌ Только для администраторов!")
            return
            
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM game_sessions WHERE is_active = 1")
        active_game = cursor.fetchone()
        
        if not active_game:
            update.message.reply_text("ℹ️ Сейчас нет активных игр!")
            conn.close()
            return
            
        end_time = datetime.now()
        cursor.execute(
            "UPDATE game_sessions SET end_time = ?, is_active = 0 WHERE is_active = 1",
            (end_time,)
        )
        conn.commit()
        conn.close()
        
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⏳ Викторина завершена! ⏳\n\n"
                 "Спасибо всем, кто принял участие! "
                 "Результаты будут объявлены в конце дня. "
                 "Вы можете посмотреть текущую таблицу лидеров с помощью /leaderboard"
        )
    
    def close_day(self, update: Update, context: CallbackContext):
        if not self._is_admin(update.effective_user.id):
            update.message.reply_text("❌ Только для администраторов!")
            return
            
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute(
            "SELECT session_id FROM game_sessions "
            "ORDER BY session_id DESC LIMIT 1"
        )
        session = cursor.fetchone()
        
        if not session:
            update.message.reply_text("ℹ️ Не было проведено ни одной игры сегодня!")
            conn.close()
            return
            
        session_id = session[0]
        today = datetime.now().date()
        
        cursor.execute('''
            SELECT u.user_id, u.username, u.first_name, 
                   SUM(CASE WHEN ua.is_correct = 1 THEN 1/ua.time_taken ELSE 0 END) as total_score
            FROM user_answers ua
            JOIN users u ON ua.user_id = u.user_id
            WHERE ua.session_id = ?
            GROUP BY u.user_id
            ORDER BY total_score DESC
            LIMIT 10
        ''', (session_id,))
        
        top_players = cursor.fetchall()
        
        if not top_players:
            update.message.reply_text("ℹ️ Сегодня никто не участвовал в игре!")
            conn.close()
            return
            
        promocodes = []
        
        for player in top_players:
            user_id = player[0]
            promocode = self._generate_promocode()
            
            cursor.execute(
                "INSERT INTO promocodes (user_id, code, date_issued) VALUES (?, ?, ?)",
                (user_id, promocode, today)
            )
            promocodes.append((player[2] or player[1] or f"ID:{player[0]}", promocode))
        
        for player in top_players:
            user_id = player[0]
            total_score = player[3]
            
            cursor.execute(
                "INSERT INTO scores (user_id, session_id, total_score, date) VALUES (?, ?, ?, ?)",
                (user_id, session_id, total_score, today)
            )

        cursor.execute(
            "DELETE FROM user_answers WHERE session_id = ?",
            (session_id,)
        )
        
        conn.commit()
        conn.close()

        leaderboard = "🏆 Топ 10 игроков сегодня: 🏆\n\n"
        for i, player in enumerate(top_players, 1):
            name = player[2] or player[1] or f"Игрок {player[0]}"
            score = round(player[3], 2)
            leaderboard += f"{i}. {name} - {score} очков\n"

        promocodes_msg = "🎁 Промокоды для топ-10 игроков: 🎁\n\n"
        for name, code in promocodes:
            promocodes_msg += f"{name}: {code}\n"
        
        promocodes_msg += "\nИспользуйте эти промокоды для получения 10% скидки в нашем магазине!"

        update.message.reply_text(leaderboard)
        update.message.reply_text(promocodes_msg)
    
    def show_leaderboard(self, update: Update, context: CallbackContext):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()

        cursor.execute(
            "SELECT session_id FROM game_sessions "
            "ORDER BY session_id DESC LIMIT 1"
        )
        session = cursor.fetchone()
        
        if not session:
            update.message.reply_text("ℹ️ Пока нет данных о результатах.")
            conn.close()
            return
            
        session_id = session[0]

        cursor.execute('''
            SELECT u.user_id, u.username, u.first_name, 
                   SUM(CASE WHEN ua.is_correct = 1 THEN 1/ua.time_taken ELSE 0 END) as total_score
            FROM user_answers ua
            JOIN users u ON ua.user_id = u.user_id
            WHERE ua.session_id = ?
            GROUP BY u.user_id
            ORDER BY total_score DESC
            LIMIT 10
        ''', (session_id,))
        
        top_players = cursor.fetchall()
        conn.close()
        
        if not top_players:
            update.message.reply_text("ℹ️ Пока никто не участвовал в текущей игре.")
            return
            
        leaderboard = "🏆 Текущая таблица лидеров: 🏆\n\n"
        for i, player in enumerate(top_players, 1):
            name = player[2] or player[1] or f"Игрок {player[0]}"
            score = round(player[3], 2)
            leaderboard += f"{i}. {name} - {score} очков\n"
        
        update.message.reply_text(leaderboard)
    
    def handle_message(self, update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        message_text = update.message.text

        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM game_sessions WHERE is_active = 1")
        active_game = cursor.fetchone()
        conn.close()
        
        if not active_game:
            update.message.reply_text(
                "ℹ️ В данный момент игра не активна. "
                "Пожалуйста, подождите, пока администратор запустит новую игру."
            )
            return

        self._start_quiz(update, user_id)
    
    def handle_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data

        if data.startswith('answer_'):
            _, question_id, selected_option = data.split('_')
            question_id = int(question_id)
            selected_option = int(selected_option)

            self._record_answer(user_id, question_id, selected_option, query)

            self._send_next_question(user_id, question_id, query)
    
    def _add_user(self, user):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
            (user.id, user.username, user.first_name, user.last_name)
        )
        
        conn.commit()
        conn.close()
    
    def _set_admin(self, user_id, is_admin):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE users SET is_admin = ? WHERE user_id = ?",
            (1 if is_admin else 0, user_id)
        )
        
        conn.commit()
        conn.close()
    
    def _is_admin(self, user_id):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT is_admin FROM users WHERE user_id = ?",
            (user_id,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        return result and result[0] == 1
    
    def _generate_promocode(self):
        letters = string.ascii_uppercase
        digits = string.digits
        return ''.join(random.choice(letters) for _ in range(3)) + ''.join(random.choice(digits) for _ in range(3))
    
    def _start_quiz(self, update: Update, user_id: int):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()

        cursor.execute("SELECT session_id FROM game_sessions WHERE is_active = 1")
        session_id = cursor.fetchone()[0]

        cursor.execute(
            "SELECT question_id FROM user_answers "
            "WHERE user_id = ? AND session_id = ? "
            "ORDER BY answer_time DESC LIMIT 1",
            (user_id, session_id)
        )
        last_question = cursor.fetchone()
        
        if last_question:
            next_question_id = last_question[0] + 1
        else:
            next_question_id = 1

        cursor.execute("SELECT COUNT(*) FROM questions")
        total_questions = cursor.fetchone()[0]
        
        conn.close()
        
        if next_question_id > total_questions:
            update.message.reply_text("🎉 Вы уже ответили на все вопросы викторины!")
            return

        self._send_question(user_id, next_question_id, update)
    
    def _send_question(self, user_id: int, question_id: int, update: Update):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()

        cursor.execute(
            "SELECT question_text, option1, option2, option3, option4 FROM questions WHERE question_id = ?",
            (question_id,)
        )
        question_data = cursor.fetchone()
        
        if not question_data:
            update.message.reply_text("Ошибка: вопрос не найден!")
            conn.close()
            return
            
        question_text, option1, option2, option3, option4 = question_data

        keyboard = [
            [InlineKeyboardButton(option1, callback_data=f'answer_{question_id}_1')],
            [InlineKeyboardButton(option2, callback_data=f'answer_{question_id}_2')],
            [InlineKeyboardButton(option3, callback_data=f'answer_{question_id}_3')],
            [InlineKeyboardButton(option4, callback_data=f'answer_{question_id}_4')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        update.message.reply_text(
            f"❓ Вопрос {question_id}:\n\n{question_text}",
            reply_markup=reply_markup
        )
        
        conn.close()
    
    def _record_answer(self, user_id: int, question_id: int, selected_option: int, query):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()

        cursor.execute(
            "SELECT correct_option FROM questions WHERE question_id = ?",
            (question_id,)
        )
        correct_option = cursor.fetchone()[0]

        cursor.execute("SELECT session_id FROM game_sessions WHERE is_active = 1")
        session_id = cursor.fetchone()[0]

        time_taken = random.uniform(1.0, 10.0)  # Placeholder - should track actual time

        cursor.execute(
            "INSERT INTO user_answers (user_id, question_id, session_id, selected_option, is_correct, answer_time, time_taken) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                question_id,
                session_id,
                selected_option,
                1 if selected_option == correct_option else 0,
                datetime.now(),
                time_taken
            )
        )
        
        conn.commit()
        conn.close()

        if selected_option == correct_option:
            query.answer("✅ Правильно!")
        else:
            query.answer("❌ Неправильно!")
    
    def _send_next_question(self, user_id: int, current_question_id: int, query):
        conn = sqlite3.connect('quiz_bot.db')
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM questions")
        total_questions = cursor.fetchone()[0]
        conn.close()
        
        next_question_id = current_question_id + 1
        
        if next_question_id > total_questions:
            query.message.reply_text(
                "🎉 Вы завершили викторину!\n\n"
                "Ваши результаты будут учтены в таблице лидеров. "
                "Итоги подводятся в конце дня командой /close."
            )
            return

        self._send_question(user_id, next_question_id, query)

    def run(self):
        self.updater.start_polling()
        self.updater.idle()

if __name__ == '__main__':
    bot = QuizBot()
    bot.run()