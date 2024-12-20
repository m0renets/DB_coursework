from flask import Flask, render_template, request
import mysql.connector
import redis
import pymysql

# Підключення до MySQL
db = pymysql.connect(
    host="localhost",       # Хост MySQL-сервера
    user="root",            # Користувач MySQL
    passwd="root-pw",       # Пароль MySQL
    db="course_work_db",    # Назва вашої бази даних
)

# Підключення до Redis
redis_client = redis.StrictRedis(host='localhost', port=6379, decode_responses=True)

# Ініціалізація Flask-застосунку
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/exams", methods=["GET", "POST"])
def exams():
    if request.method == "POST":
        specialization = request.form["specialization"]
        semester = request.form["semester"]

        try:
            cursor = db.cursor()
            query = """
            SELECT c.name, t.name AS teacher, c.reporting
            FROM courses c
            JOIN teachers t ON c.teacher_id = t.id
            WHERE c.specialization_code = %s AND c.semester = %s
            """

            cursor.execute(query, (specialization, semester))
            exams_list = cursor.fetchall()
            cursor.close()

            if not exams_list:
                return f"No data found for specialization={specialization}, semester={semester}"

        except mysql.connector.Error as err:
            return f"MySQL Error: {err}"

        return render_template("exams.html", exams=exams_list, specialization=specialization, semester=semester)
    return render_template("exams.html")

@app.route("/student_scores", methods=["GET", "POST"])
def student_scores():
    if request.method == "POST":
        student_name = request.form["student_name"]

        try:
            # Отримання інформації про студента
            cursor = db.cursor()
            student_query = """
            SELECT id, name, group_name FROM students WHERE name = %s
            """
            cursor.execute(student_query, (student_name,))
            student = cursor.fetchone()

            if not student:
                return f"Student {student_name} not found."

            student_id, student_full_name, group_name = student

            # Отримання балів із Redis
            scores_data = redis_client.get(f"student:{student_id}")
            scores = eval(scores_data) if scores_data else []  # Формат: [[course_id, score], ...]

            if not scores:
                return f"No scores found for student {student_name}."

            # Отримання інформації про курси
            course_ids = [score[0] for score in scores]  # ID курсів із Redis
            course_query = f"""
            SELECT id, name, teacher_id, reporting FROM courses WHERE id IN ({','.join(map(str, course_ids))})
            """
            cursor.execute(course_query)
            courses = cursor.fetchall()  # Отримуємо курси за ID

            # Отримання імен викладачів
            teacher_ids = [course[2] for course in courses]
            teacher_query = f"""
            SELECT id, name FROM teachers WHERE id IN ({','.join(map(str, teacher_ids))})
            """
            cursor.execute(teacher_query)
            teachers = dict(cursor.fetchall())  # Створюємо словник {id: name} для швидкого пошуку

            # Зіставлення даних про курси та оцінки
            performance_with_scores = []
            total_score = 0

            for course_id, score in scores:
                course = next((c for c in courses if c[0] == course_id), None)
                if course:
                    course_name, teacher_id, reporting = course[1], course[2], course[3]
                    teacher_name = teachers.get(teacher_id, "Unknown")
                    performance_with_scores.append((course_name, teacher_name, reporting, score))
                    total_score += score

            # Підрахунок середнього бала
            avg_score = round(total_score / len(scores), 2) if scores else 0.00

            cursor.close()

        except mysql.connector.Error as e:
            return f"MySQL Error: {e}"
        except redis.ConnectionError as e:
            return f"Redis Error: {e}"

        return render_template(
            "scores.html",
            performance=performance_with_scores,
            student_name=student_full_name,
            group_name=group_name,
            avg_score=avg_score
        )

    return render_template("scores.html")

@app.route("/generate_diploma", methods=["POST"])
def generate_diploma():
    student_name = request.form.get("student_name")
    try:
        # Отримання інформації про студента
        cursor = db.cursor()
        student_query = """
        SELECT id, name, group_name FROM students WHERE name = %s
        """
        cursor.execute(student_query, (student_name,))
        student = cursor.fetchone()

        if not student:
            return f"Student {student_name} not found."

        student_id, student_full_name, group_name = student

        # Отримання балів із Redis
        scores_data = redis_client.get(f"student:{student_id}")
        scores = eval(scores_data) if scores_data else []  # Формат: [[course_id, score], ...]

        if not scores:
            return f"No scores found for student {student_name}."

        # Отримання інформації про курси
        course_ids = [score[0] for score in scores]  # ID курсів із Redis
        course_query = f"""
        SELECT id, name FROM courses WHERE id IN ({','.join(map(str, course_ids))})
        """
        cursor.execute(course_query)
        courses = cursor.fetchall()  # Отримуємо курси за ID

        # Зіставлення курсів і оцінок
        course_details = [
            {"name": course[1], "score": next(score[1] for score in scores if score[0] == course[0])}
            for course in courses
        ]
        total_score = sum(score["score"] for score in course_details)
        avg_score = round(total_score / len(course_details), 2) if course_details else 0.00

        cursor.close()

        # Повертаємо сторінку диплома
        return render_template(
            "diploma.html",
            student_name=student_full_name,
            group_name=group_name,
            avg_score=avg_score,
            courses=course_details
        )

    except mysql.connector.Error as e:
        return f"MySQL Error: {e}"
    except redis.ConnectionError as e:
        return f"Redis Error: {e}"

@app.route("/update_score", methods=["GET", "POST"])
def update_score():
    message = None  # Змінна для повідомлення
    courses = []
    student_name = request.args.get("student_name", "") if request.method == "GET" else request.form.get("student_name")

    if request.method == "POST":
        # Обробка форми для оновлення оцінки
        course_id = request.form.get("course_id")
        new_score = request.form.get("score")

        if not course_id or not new_score:
            message = "Error: All fields are required."
        else:
            try:
                course_id = int(course_id)
                new_score = int(new_score)

                # Отримання ID студента
                cursor = db.cursor()
                student_query = "SELECT id FROM students WHERE name = %s"
                cursor.execute(student_query, (student_name,))
                student = cursor.fetchone()

                if not student:
                    message = f"Student {student_name} not found."
                else:
                    student_id = student[0]

                    # Оновлення оцінки в Redis
                    scores_data = redis_client.get(f"student:{student_id}")
                    scores = eval(scores_data) if scores_data else []

                    # Зміна оцінки для вибраного курсу
                    for score in scores:
                        if score[0] == course_id:
                            score[1] = new_score
                            break
                    else:
                        scores.append([course_id, new_score])  # Якщо курс не знайдено, додаємо новий

                    # Збереження змін у Redis
                    redis_client.set(f"student:{student_id}", str(scores))
                    message = f"Score updated successfully for student {student_name}."

                cursor.close()

            except ValueError:
                message = "Invalid input: Score and course ID must be numbers."
            except mysql.connector.Error as e:
                message = f"MySQL Error: {e}"
            except redis.ConnectionError as e:
                message = f"Redis Error: {e}"

    elif request.method == "GET" and student_name:
        try:
            cursor = db.cursor()
            student_query = "SELECT id FROM students WHERE name = %s"
            cursor.execute(student_query, (student_name,))
            student = cursor.fetchone()

            if student:
                student_id = student[0]

                # Отримання курсів студента
                scores_data = redis_client.get(f"student:{student_id}")
                scores = eval(scores_data) if scores_data else []
                course_ids = [score[0] for score in scores]

                if course_ids:  # Перевіряємо, чи є курси
                    course_query = f"SELECT id, name FROM courses WHERE id IN ({','.join(map(str, course_ids))})"
                    cursor.execute(course_query)
                    courses = cursor.fetchall()

            cursor.close()
        except mysql.connector.Error as e:
            message = f"MySQL Error: {e}"
        except redis.ConnectionError as e:
            message = f"Redis Error: {e}"

    return render_template("update_score.html", student_name=student_name, courses=courses, message=message)

@app.route("/rating")
def rating():
    try:
        cursor = db.cursor()

        # Отримання даних про студентів
        student_query = """
        SELECT id, name, group_name FROM students
        """
        cursor.execute(student_query)
        students = cursor.fetchall()

        # Формування списку з середніми балами
        rating_list = []
        for student in students:
            student_id, student_name, group_name = student

            # Отримання балів із Redis
            scores_data = redis_client.get(f"student:{student_id}")
            scores = eval(scores_data) if scores_data else []

            if scores:
                total_score = sum(score[1] for score in scores)
                avg_score = total_score / len(scores)
            else:
                avg_score = 0

            rating_list.append((student_name, group_name, avg_score))

        # Сортування за середнім балом у спадному порядку
        rating_list.sort(key=lambda x: x[2], reverse=True)

        # Додавання номера в рейтингу
        ranked_list = [(i + 1, student[0], student[1], round(student[2], 2)) for i, student in enumerate(rating_list)]

        cursor.close()
        return render_template("rating.html", ranked_list=ranked_list)

    except mysql.connector.Error as e:
        return f"MySQL Error: {e}"
    except redis.ConnectionError as e:
        return f"Redis Error: {e}"

if __name__ == "__main__":
    app.run(debug=True)
