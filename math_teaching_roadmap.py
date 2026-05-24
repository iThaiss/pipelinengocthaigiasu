import argparse
import html
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("local_curriculum")

WEEK_SPECS: list[dict[str, Any]] = [
    {
        "week": 1,
        "phase": "Chẩn đoán và bù nền",
        "canonical_unit_ids": [18, 19, 20, 21, 22],
        "lesson_focus": "Chẩn đoán đầu vào; củng cố giới hạn, liên tục, đạo hàm và phương trình/bất phương trình đạo hàm.",
        "assessment": "diagnostic_test",
    },
    {
        "week": 2,
        "phase": "Đạo hàm và khảo sát hàm số",
        "canonical_unit_ids": [1, 2],
        "lesson_focus": "Tính đơn điệu, cực trị, đọc bảng biến thiên và đồ thị đạo hàm.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 3,
        "phase": "Đạo hàm và khảo sát hàm số",
        "canonical_unit_ids": [3, 50],
        "lesson_focus": "GTLN-GTNN và mở rộng sang hàm lượng giác, mũ, logarit khi cần.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 4,
        "phase": "Đạo hàm và khảo sát hàm số",
        "canonical_unit_ids": [4, 51],
        "lesson_focus": "Tiệm cận đứng, ngang, xiên và cách đọc nhanh từ hàm số/đồ thị.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 5,
        "phase": "Đạo hàm và khảo sát hàm số",
        "canonical_unit_ids": [5, 52],
        "lesson_focus": "Khảo sát hàm số, nhận dạng đồ thị và hàm phân thức bậc hai.",
        "assessment": "mini_test",
    },
    {
        "week": 6,
        "phase": "Đạo hàm và khảo sát hàm số",
        "canonical_unit_ids": [1, 2, 3, 4, 5],
        "lesson_focus": "Tổng hợp chủ đề hàm số: trộn dạng bảng biến thiên, đồ thị, cực trị, tiệm cận, GTLN-GTNN.",
        "assessment": "topic_test",
    },
    {
        "week": 7,
        "phase": "Ứng dụng đạo hàm thực tế",
        "canonical_unit_ids": [6, 45, 53],
        "lesson_focus": "Mô hình hóa bài toán thực tế, kinh tế, doanh thu, lợi nhuận và tối ưu chi phí.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 8,
        "phase": "Ứng dụng đạo hàm thực tế",
        "canonical_unit_ids": [46, 47, 49],
        "lesson_focus": "Tốc độ thay đổi, ứng dụng khảo sát đồ thị, quãng đường ngắn nhất và thời gian ngắn nhất.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 9,
        "phase": "Ứng dụng đạo hàm thực tế",
        "canonical_unit_ids": [48, 54],
        "lesson_focus": "Tối ưu diện tích, thể tích và bài toán hình học không gian ứng dụng đạo hàm.",
        "assessment": "topic_test",
    },
    {
        "week": 10,
        "phase": "Nguyên hàm và tích phân",
        "canonical_unit_ids": [7, 33, 34],
        "lesson_focus": "Nguyên hàm cơ bản, nguyên hàm lượng giác/hàm đặc biệt và nguyên hàm cho bởi nhiều công thức.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 11,
        "phase": "Nguyên hàm và tích phân",
        "canonical_unit_ids": [8, 27, 28],
        "lesson_focus": "Tích phân, tính chất, hàm phân thức hữu tỉ và ý nghĩa hình học.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 12,
        "phase": "Nguyên hàm và tích phân",
        "canonical_unit_ids": [9, 31],
        "lesson_focus": "Diện tích hình phẳng và bài toán diện tích thực tế.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 13,
        "phase": "Nguyên hàm và tích phân",
        "canonical_unit_ids": [10, 32],
        "lesson_focus": "Thể tích vật thể, thể tích thực tế và cách nhận mô hình tính tích phân.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 14,
        "phase": "Nguyên hàm và tích phân",
        "canonical_unit_ids": [29, 30, 7, 8, 9, 10],
        "lesson_focus": "Tích phân trong bài toán tốc độ thay đổi, chuyển động và tổng ôn nguyên hàm - tích phân.",
        "assessment": "topic_test",
    },
    {
        "week": 15,
        "phase": "Vectơ và Oxyz",
        "canonical_unit_ids": [11, 12, 13, 55, 58],
        "lesson_focus": "Vectơ, hệ trục Oxyz, biểu thức tọa độ, tích có hướng và tâm tỉ cự.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 16,
        "phase": "Vectơ và Oxyz",
        "canonical_unit_ids": [14, 39],
        "lesson_focus": "Phương trình mặt phẳng, vị trí tương đối và ứng dụng thực tế của mặt phẳng.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 17,
        "phase": "Vectơ và Oxyz",
        "canonical_unit_ids": [15, 35, 56, 57],
        "lesson_focus": "Phương trình đường thẳng, tham số hóa, góc/khoảng cách/vị trí tương đối và toán thực tế Oxyz.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 18,
        "phase": "Vectơ và Oxyz",
        "canonical_unit_ids": [16, 36, 37, 38],
        "lesson_focus": "Góc, khoảng cách, hình chiếu, đối xứng và phương pháp gắn hệ trục tọa độ.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 19,
        "phase": "Vectơ và Oxyz",
        "canonical_unit_ids": [17, 40, 41, 42],
        "lesson_focus": "Mặt cầu, vị trí tương đối, cực trị Oxyz và ứng dụng thực tế của vectơ.",
        "assessment": "topic_test",
    },
    {
        "week": 20,
        "phase": "Thống kê và xác suất",
        "canonical_unit_ids": [23, 24],
        "lesson_focus": "Mẫu số liệu ghép nhóm, khoảng biến thiên, tứ phân vị, phương sai và độ lệch chuẩn.",
        "assessment": "weekly_quiz",
    },
    {
        "week": 21,
        "phase": "Thống kê và xác suất",
        "canonical_unit_ids": [25, 26, 43, 44, 59],
        "lesson_focus": "Xác suất có điều kiện, công thức nhân, xác suất toàn phần, Bayes và sơ đồ cây.",
        "assessment": "topic_test",
    },
    {
        "week": 22,
        "phase": "Tổng ôn THPTQG",
        "canonical_unit_ids": [1, 2, 3, 5, 7, 8, 14, 15, 23, 25],
        "lesson_focus": "Tổng ôn dạng đề THPTQG, trộn chủ đề và ưu tiên các unit có nhiều câu hỏi.",
        "assessment": "mixed_topic_test",
    },
    {
        "week": 23,
        "phase": "Luyện đề hoàn chỉnh",
        "canonical_unit_ids": [1, 2, 3, 4, 5, 7, 8, 11, 13, 14, 15, 17, 25, 26],
        "lesson_focus": "Luyện đề hoàn chỉnh số 1-2, phân tích lỗi theo chủ đề và kỹ thuật quản lý thời gian.",
        "assessment": "full_mock_exam",
    },
    {
        "week": 24,
        "phase": "Luyện đề hoàn chỉnh",
        "canonical_unit_ids": [6, 9, 10, 23, 24, 25, 26, 35, 36, 37, 38, 40, 41, 43, 44],
        "lesson_focus": "Luyện đề hoàn chỉnh số 3-4, cá nhân hóa bù hổng và chốt chiến lược trước kỳ thi.",
        "assessment": "full_mock_exam",
    },
]

PRACTICE_POLICY_BY_ASSESSMENT = {
    "diagnostic_test": {"foundational": 20, "application": 10, "advanced": 5},
    "weekly_quiz": {"foundational": 24, "application": 14, "advanced": 4},
    "mini_test": {"foundational": 28, "application": 18, "advanced": 6},
    "topic_test": {"foundational": 32, "application": 24, "advanced": 8},
    "mixed_topic_test": {"foundational": 30, "application": 30, "advanced": 10},
    "full_mock_exam": {"foundational": 18, "application": 24, "advanced": 8},
    "foundation_checkpoint": {"foundational": 18, "comprehension": 12, "application": 6, "advanced": 0},
}

ASSESSMENT_LABELS = {
    "diagnostic_test": "Bài chẩn đoán đầu vào",
    "weekly_quiz": "Quiz cuối tuần",
    "mini_test": "Mini test giữa chặng",
    "topic_test": "Bài kiểm tra theo chủ đề",
    "mixed_topic_test": "Bài tổng ôn trộn chủ đề",
    "full_mock_exam": "Đề luyện hoàn chỉnh",
    "foundation_checkpoint": "Checkpoint nền tảng",
}

TRACK_LABELS = {
    "algebra_analysis_probability": "Đại - Giải tích - Xác suất",
    "geometry_oxyz": "Hình - Oxyz",
    "mixed_mock": "Luyện đề tổng hợp",
}

SELF_STUDY_VIDEO_UNIT_IDS = {18, 19, 20}

SELF_STUDY_VIDEO_UNITS = [
    {"canonical_unit_id": 18, "title": "Giới hạn hàm số khi x → a", "delivery": "video_recording"},
    {"canonical_unit_id": 19, "title": "Giới hạn của hàm số khi x → ∞", "delivery": "video_recording"},
    {"canonical_unit_id": 20, "title": "Hàm số liên tục", "delivery": "video_recording"},
]

SESSION_WEEK_SPECS: list[dict[str, Any]] = [
    {"week": 1, "phase": "Chẩn đoán và bù nền đạo hàm - vectơ", "assessment": "diagnostic_test", "lesson_focus": "Chẩn đoán đầu vào; củng cố đạo hàm, phương trình/bất phương trình đạo hàm và vectơ nền tảng.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [21], "lesson_focus": "Đạo hàm và các quy tắc tính đạo hàm cần dùng cho khảo sát hàm số.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [11], "lesson_focus": "Vectơ trong không gian và các phép toán nền tảng.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [22], "lesson_focus": "Phương trình, bất phương trình đạo hàm và bài chẩn đoán năng lực đầu vào.", "assessment_role": "diagnostic"},
    ]},
    {"week": 2, "phase": "Đạo hàm song song Oxyz nền tảng", "assessment": "weekly_quiz", "lesson_focus": "Tính đơn điệu, cực trị và hệ trục tọa độ Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [1], "lesson_focus": "Tính đơn điệu, xét dấu đạo hàm và đọc bảng biến thiên.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [12], "lesson_focus": "Hệ trục tọa độ Oxyz, tọa độ điểm và vectơ.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [2], "lesson_focus": "Cực trị hàm số, điều kiện cần/đủ và lỗi đọc nhầm cực trị.", "assessment_role": "practice"},
    ]},
    {"week": 3, "phase": "Đạo hàm song song Oxyz nền tảng", "assessment": "weekly_quiz", "lesson_focus": "GTLN-GTNN, luyện trộn đạo hàm và biểu thức tọa độ vectơ.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [3], "lesson_focus": "GTLN-GTNN trên đoạn/khoảng và từ bảng biến thiên.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [13], "lesson_focus": "Biểu thức tọa độ của phép toán vectơ.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [1, 2, 3], "lesson_focus": "Luyện trộn đơn điệu, cực trị, GTLN-GTNN.", "assessment_role": "quiz"},
    ]},
    {"week": 4, "phase": "Đạo hàm song song Oxyz nền tảng", "assessment": "weekly_quiz", "lesson_focus": "Tiệm cận, khảo sát hàm số bước đầu, tích có hướng và tâm tỉ cự.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [4, 51], "lesson_focus": "Tiệm cận đứng, ngang, xiên và cách đọc nhanh từ hàm số/đồ thị.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [55, 58], "lesson_focus": "Tích có hướng, ứng dụng và bài toán tâm tỉ cự.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [5], "lesson_focus": "Khung khảo sát hàm số và liên hệ các yếu tố đã học.", "assessment_role": "practice"},
    ]},
    {"week": 5, "phase": "Khảo sát hàm số song song mặt phẳng", "assessment": "mini_test", "lesson_focus": "Mở rộng đạo hàm cho hàm đặc biệt, tiệm cận xiên và phương trình mặt phẳng.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [50], "lesson_focus": "Đơn điệu, cực trị, GTLN-GTNN của hàm lượng giác, mũ và logarit.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [14], "lesson_focus": "Phương trình mặt phẳng cơ bản và các dạng nhận diện nhanh.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [51], "lesson_focus": "Tiệm cận xiên và luyện dạng tiệm cận nâng cao.", "assessment_role": "practice"},
    ]},
    {"week": 6, "phase": "Khảo sát hàm số song song mặt phẳng", "assessment": "topic_test", "lesson_focus": "Khảo sát hàm phân thức, ứng dụng mặt phẳng và tổng hợp chủ đề hàm số.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [52], "lesson_focus": "Khảo sát đồ thị hàm phân thức bậc hai.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [39], "lesson_focus": "Ứng dụng thực tế của phương trình mặt phẳng trong không gian.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [1, 2, 3, 4, 5], "lesson_focus": "Tổng hợp hàm số: bảng biến thiên, đồ thị, cực trị, tiệm cận, GTLN-GTNN.", "assessment_role": "quiz"},
    ]},
    {"week": 7, "phase": "Ứng dụng đạo hàm song song vectơ thực tế", "assessment": "weekly_quiz", "lesson_focus": "Ứng dụng đạo hàm thực tế, kinh tế và vectơ thực tế.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [6], "lesson_focus": "Quy trình mô hình hóa bài toán thực tế bằng đạo hàm.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [42], "lesson_focus": "Ứng dụng thực tế của vectơ trong không gian.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [45, 53], "lesson_focus": "Bài toán kinh tế, doanh thu, lợi nhuận và tối ưu chi phí.", "assessment_role": "practice"},
    ]},
    {"week": 8, "phase": "Ứng dụng đạo hàm song song mặt phẳng", "assessment": "weekly_quiz", "lesson_focus": "Tốc độ thay đổi, khảo sát thực tế và luyện mặt phẳng/tích có hướng.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [46], "lesson_focus": "Tốc độ thay đổi và các đại lượng biến thiên theo thời gian.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [14, 55], "lesson_focus": "Luyện mặt phẳng kết hợp tích có hướng.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [47, 49], "lesson_focus": "Ứng dụng khảo sát đồ thị, quãng đường ngắn nhất và thời gian ngắn nhất.", "assessment_role": "practice"},
    ]},
    {"week": 9, "phase": "Ứng dụng đạo hàm song song đường thẳng", "assessment": "topic_test", "lesson_focus": "Tối ưu bằng đạo hàm và mở mạch phương trình đường thẳng Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [48], "lesson_focus": "Tối ưu diện tích, thể tích và bài toán cực trị hình học.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [15], "lesson_focus": "Phương trình đường thẳng trong không gian.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [54], "lesson_focus": "Bài toán hình học không gian ứng dụng đạo hàm.", "assessment_role": "quiz"},
    ]},
    {"week": 10, "phase": "Nguyên hàm song song đường thẳng", "assessment": "weekly_quiz", "lesson_focus": "Nguyên hàm cơ bản/nâng cao song song phương pháp tham số hóa.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [7], "lesson_focus": "Nguyên hàm cơ bản và tính chất của nguyên hàm.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [35], "lesson_focus": "Phương pháp tham số hóa trong Oxyz.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [33, 34], "lesson_focus": "Nguyên hàm lượng giác, hàm đặc biệt và nguyên hàm cho bởi nhiều công thức.", "assessment_role": "practice"},
    ]},
    {"week": 11, "phase": "Tích phân song song đường thẳng", "assessment": "weekly_quiz", "lesson_focus": "Tích phân cơ bản/nâng cao song song đường thẳng Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [8], "lesson_focus": "Tích phân và tính chất của tích phân.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [56], "lesson_focus": "Đường thẳng: góc, khoảng cách và vị trí tương đối.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [27, 28], "lesson_focus": "Tích phân hàm phân thức hữu tỉ và ý nghĩa hình học.", "assessment_role": "practice"},
    ]},
    {"week": 12, "phase": "Tích phân ứng dụng song song đường thẳng thực tế", "assessment": "weekly_quiz", "lesson_focus": "Diện tích, chuyển động bằng tích phân và toán thực tế đường thẳng Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [9, 31], "lesson_focus": "Diện tích hình phẳng và bài toán diện tích thực tế.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [57], "lesson_focus": "Toán thực tế Oxyz với phương trình đường thẳng.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [29, 30], "lesson_focus": "Tốc độ thay đổi, chuyển động và mô hình tích phân.", "assessment_role": "practice"},
    ]},
    {"week": 13, "phase": "Tích phân ứng dụng song song góc-khoảng cách", "assessment": "weekly_quiz", "lesson_focus": "Thể tích bằng tích phân, tổng hợp tích phân và góc trong không gian.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [10, 32], "lesson_focus": "Thể tích vật thể, thể tích thực tế và cách nhận mô hình tích phân.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [16], "lesson_focus": "Các công thức tính góc trong không gian.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [7, 8, 9, 10], "lesson_focus": "Luyện trộn nguyên hàm, tích phân, diện tích và thể tích.", "assessment_role": "quiz"},
    ]},
    {"week": 14, "phase": "Tổng hợp tích phân song song hình chiếu", "assessment": "topic_test", "lesson_focus": "Tổng hợp tích phân ứng dụng song song hình chiếu/đối xứng Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [7, 8], "lesson_focus": "Tổng hợp nguyên hàm - tích phân cơ bản.", "assessment_role": "practice"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [36], "lesson_focus": "Hình chiếu vuông góc và đối xứng trong Oxyz.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [29, 30, 31, 32], "lesson_focus": "Tổng hợp ứng dụng tích phân: tốc độ, chuyển động, diện tích, thể tích.", "assessment_role": "quiz"},
    ]},
    {"week": 15, "phase": "Thống kê song song mặt cầu", "assessment": "weekly_quiz", "lesson_focus": "Thống kê mẫu số liệu ghép nhóm song song phương trình mặt cầu.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [23], "lesson_focus": "Khoảng biến thiên và khoảng tứ phân vị của mẫu số liệu ghép nhóm.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [17], "lesson_focus": "Phương trình mặt cầu cơ bản.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [24], "lesson_focus": "Phương sai và độ lệch chuẩn của mẫu số liệu ghép nhóm.", "assessment_role": "practice"},
    ]},
    {"week": 16, "phase": "Xác suất song song mặt cầu nâng cao", "assessment": "weekly_quiz", "lesson_focus": "Xác suất có điều kiện/Bayes song song mặt cầu nâng cao.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [25], "lesson_focus": "Xác suất có điều kiện và công thức nhân xác suất.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [40], "lesson_focus": "Mặt cầu nâng cao, vị trí tương đối với mặt phẳng và đường thẳng.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [26], "lesson_focus": "Xác suất toàn phần và công thức Bayes.", "assessment_role": "practice"},
    ]},
    {"week": 17, "phase": "Xác suất nâng cao song song góc-khoảng cách", "assessment": "weekly_quiz", "lesson_focus": "Xác suất nâng cao song song góc/khoảng cách Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [43], "lesson_focus": "Tính xác suất bằng công thức nhân tổng quát.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [37], "lesson_focus": "Viết phương trình đường thẳng, mặt phẳng liên quan góc và khoảng cách.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [44, 59], "lesson_focus": "Sơ đồ cây và Bayes trong bài toán xác suất nâng cao.", "assessment_role": "quiz"},
    ]},
    {"week": 18, "phase": "Tổng hợp xác suất-thống kê song song gắn trục", "assessment": "weekly_quiz", "lesson_focus": "Tổng hợp xác suất-thống kê song song phương pháp gắn trục Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [23, 24], "lesson_focus": "Tổng hợp thống kê mẫu số liệu ghép nhóm.", "assessment_role": "practice"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [38], "lesson_focus": "Phương pháp gắn hệ trục tọa độ Oxyz vào hình học không gian.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [25, 26, 43, 44, 59], "lesson_focus": "Tổng hợp xác suất có điều kiện, Bayes và sơ đồ cây.", "assessment_role": "quiz"},
    ]},
    {"week": 19, "phase": "Tổng hợp ứng dụng song song cực trị Oxyz", "assessment": "topic_test", "lesson_focus": "Tổng hợp ứng dụng đạo hàm song song cực trị Oxyz.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [6, 45, 46], "lesson_focus": "Tổng hợp ứng dụng đạo hàm: mô hình, kinh tế, tốc độ thay đổi.", "assessment_role": "practice"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [41], "lesson_focus": "Một số mô hình cực trị Oxyz.", "assessment_role": "learn"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [48, 49, 53, 54], "lesson_focus": "Tổng hợp tối ưu, quãng đường/thời gian và bài toán hình học ứng dụng đạo hàm.", "assessment_role": "quiz"},
    ]},
    {"week": 20, "phase": "Tổng hợp tích phân-xác suất song song Oxyz vận dụng", "assessment": "weekly_quiz", "lesson_focus": "Tổng hợp tích phân, xác suất-thống kê và Oxyz vận dụng.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [7, 8, 9, 10], "lesson_focus": "Tổng hợp nguyên hàm, tích phân, diện tích và thể tích.", "assessment_role": "practice"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [35, 36, 37, 38, 40, 41], "lesson_focus": "Tổng hợp Oxyz vận dụng: tham số, hình chiếu, góc/khoảng cách, gắn trục, mặt cầu, cực trị.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [23, 24, 25, 26], "lesson_focus": "Tổng hợp thống kê và xác suất lõi.", "assessment_role": "quiz"},
    ]},
    {"week": 21, "phase": "Tổng ôn THPTQG song song", "assessment": "topic_test", "lesson_focus": "Tổng ôn Đại/Giải tích và Hình/Oxyz theo cấu trúc đề.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [1, 2, 3, 4, 5], "lesson_focus": "Tổng ôn hàm số và ứng dụng đạo hàm.", "assessment_role": "review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [11, 12, 13, 14, 15, 16, 17], "lesson_focus": "Tổng ôn Oxyz nền tảng: vectơ, mặt phẳng, đường thẳng, góc, mặt cầu.", "assessment_role": "review"},
        {"session_no": 3, "track": "mixed_mock", "canonical_unit_ids": [7, 8, 23, 25], "lesson_focus": "Mini mock trộn hàm số, tích phân, thống kê và xác suất.", "assessment_role": "mock"},
    ]},
    {"week": 22, "phase": "Tổng ôn THPTQG", "assessment": "mixed_topic_test", "lesson_focus": "Tổng ôn ứng dụng đạo hàm, Oxyz vận dụng, tích phân và xác suất.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [6, 45, 46, 47, 48, 49], "lesson_focus": "Tổng ôn ứng dụng đạo hàm thực tế và tối ưu.", "assessment_role": "review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [35, 36, 37, 38], "lesson_focus": "Tổng ôn Oxyz vận dụng: tham số, hình chiếu, góc/khoảng cách, gắn trục.", "assessment_role": "review"},
        {"session_no": 3, "track": "mixed_mock", "canonical_unit_ids": [9, 10, 26, 43, 44], "lesson_focus": "Mini mock trộn tích phân ứng dụng, Bayes và sơ đồ cây.", "assessment_role": "mock"},
    ]},
    {"week": 23, "phase": "Luyện đề hoàn chỉnh", "assessment": "full_mock_exam", "lesson_focus": "Luyện đề hoàn chỉnh số 1-2, phân tích lỗi theo Đại/Hình và kỹ thuật quản lý thời gian.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [1, 2, 3, 7, 8, 25, 26], "lesson_focus": "Chữa lỗi Đại/Giải tích/Xác suất từ đề luyện số 1.", "assessment_role": "review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [14, 15, 17, 40, 41], "lesson_focus": "Chữa lỗi Hình/Oxyz từ đề luyện số 1.", "assessment_role": "review"},
        {"session_no": 3, "track": "mixed_mock", "canonical_unit_ids": [1, 5, 11, 13, 23, 25], "lesson_focus": "Đề luyện hoàn chỉnh số 2 và phân tích lỗi theo nhóm kiến thức.", "assessment_role": "mock"},
    ]},
    {"week": 24, "phase": "Luyện đề hoàn chỉnh", "assessment": "full_mock_exam", "lesson_focus": "Luyện đề hoàn chỉnh số 3-4, cá nhân hóa bù hổng và chốt chiến lược trước kỳ thi.", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [6, 9, 10, 23, 24, 25, 26], "lesson_focus": "Chữa lỗi Đại/Giải tích/Xác suất từ đề luyện số 3.", "assessment_role": "review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [35, 36, 37, 38, 40, 41, 42], "lesson_focus": "Chữa lỗi Hình/Oxyz từ đề luyện số 3 và chốt chiến thuật hình học.", "assessment_role": "review"},
        {"session_no": 3, "track": "mixed_mock", "canonical_unit_ids": [43, 44, 48, 49, 53, 54, 57], "lesson_focus": "Đề luyện hoàn chỉnh số 4, cá nhân hóa bù hổng và chốt chiến lược trước kỳ thi.", "assessment_role": "mock"},
    ]},
]

FOUNDATION_CHECKPOINT_WEEKS = {4, 8, 12, 16, 20, 24}

FOUNDATION_WEEK_OVERRIDES: dict[int, dict[str, Any]] = {
    4: {"phase": "Checkpoint 1: đạo hàm nền và vectơ", "lesson_focus": "Củng cố đạo hàm nền, vectơ và kỹ năng tự kiểm tra sau 3 tuần đầu.", "assessment": "foundation_checkpoint", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [21, 22, 1], "lesson_focus": "Củng cố đạo hàm, phương trình/bất phương trình đạo hàm và tính đơn điệu mức nền.", "assessment_role": "checkpoint_review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [11, 12, 13, 58], "lesson_focus": "Củng cố vectơ, hệ trục tọa độ, biểu thức tọa độ và tâm tỉ cự ở mức nền.", "assessment_role": "checkpoint_review"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [1, 2, 3], "lesson_focus": "Quiz nền tảng và chữa lỗi đơn điệu, cực trị, GTLN-GTNN.", "assessment_role": "checkpoint_quiz"},
    ]},
    8: {"phase": "Checkpoint 2: hàm số và mặt phẳng", "lesson_focus": "Củng cố đơn điệu, cực trị, GTLN-GTNN, tiệm cận và mặt phẳng.", "assessment": "foundation_checkpoint", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [1, 2, 3, 4, 50], "lesson_focus": "Ôn lặp hàm số: đơn điệu, cực trị, GTLN-GTNN, tiệm cận.", "assessment_role": "checkpoint_review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [14, 39, 55], "lesson_focus": "Củng cố phương trình mặt phẳng và tích có hướng ở mức nền.", "assessment_role": "checkpoint_review"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [5, 51, 52], "lesson_focus": "Quiz nền tảng và chữa lỗi khảo sát hàm số/tiệm cận.", "assessment_role": "checkpoint_quiz"},
    ]},
    12: {"phase": "Checkpoint 3: ứng dụng đạo hàm và đường thẳng", "lesson_focus": "Củng cố ứng dụng đạo hàm, mô hình hóa cơ bản và đường thẳng Oxyz.", "assessment": "foundation_checkpoint", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [6, 45, 46, 48], "lesson_focus": "Ôn lặp ứng dụng đạo hàm: mô hình, kinh tế, tốc độ, tối ưu.", "assessment_role": "checkpoint_review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [15, 35, 56, 57], "lesson_focus": "Củng cố đường thẳng, tham số hóa, góc/khoảng cách và toán thực tế Oxyz nhẹ.", "assessment_role": "checkpoint_review"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [47, 49, 53, 54], "lesson_focus": "Quiz nền tảng và chữa lỗi ứng dụng khảo sát/tối ưu.", "assessment_role": "checkpoint_quiz"},
    ]},
    16: {"phase": "Checkpoint 4: nguyên hàm - tích phân và góc/khoảng cách", "lesson_focus": "Củng cố nguyên hàm, tích phân cơ bản và hình học góc/khoảng cách/hình chiếu.", "assessment": "foundation_checkpoint", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [7, 8, 27, 28, 33, 34], "lesson_focus": "Ôn lặp nguyên hàm, tích phân và các biến thể cơ bản.", "assessment_role": "checkpoint_review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [16, 36, 37], "lesson_focus": "Củng cố góc, khoảng cách, hình chiếu và đối xứng Oxyz.", "assessment_role": "checkpoint_review"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [7, 8, 9, 10], "lesson_focus": "Quiz nền tảng và chữa lỗi nguyên hàm - tích phân.", "assessment_role": "checkpoint_quiz"},
    ]},
    20: {"phase": "Checkpoint 5: tích phân ứng dụng và Oxyz vận dụng nhẹ", "lesson_focus": "Củng cố diện tích, thể tích, chuyển động, mặt cầu và gắn trục Oxyz.", "assessment": "foundation_checkpoint", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [9, 10, 29, 30, 31, 32], "lesson_focus": "Ôn lặp tích phân ứng dụng: diện tích, thể tích, tốc độ và chuyển động.", "assessment_role": "checkpoint_review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [17, 38, 40, 41], "lesson_focus": "Củng cố mặt cầu, gắn trục và mô hình cực trị Oxyz ở mức nền.", "assessment_role": "checkpoint_review"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [23, 24, 25, 26], "lesson_focus": "Quiz nền tảng mở đầu thống kê/xác suất và chữa lỗi tích phân ứng dụng.", "assessment_role": "checkpoint_quiz"},
    ]},
    21: {"phase": "Thống kê nền tảng song song Oxyz vận dụng nhẹ", "lesson_focus": "Học thống kê mẫu số liệu ghép nhóm và duy trì Oxyz vận dụng nhẹ.", "assessment": "weekly_quiz", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [23], "lesson_focus": "Khoảng biến thiên và khoảng tứ phân vị của mẫu số liệu ghép nhóm.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [38, 41], "lesson_focus": "Ôn gắn trục và mô hình cực trị Oxyz nhẹ, không nâng lên luyện đề.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [24], "lesson_focus": "Phương sai, độ lệch chuẩn và ôn lặp thống kê bằng truy hồi kiến thức.", "assessment_role": "practice"},
    ]},
    22: {"phase": "Xác suất nền tảng song song Oxyz vận dụng nhẹ", "lesson_focus": "Học xác suất có điều kiện, công thức nhân và duy trì hình học tọa độ.", "assessment": "weekly_quiz", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [25], "lesson_focus": "Xác suất có điều kiện và công thức nhân xác suất.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [35, 36, 37], "lesson_focus": "Ôn tham số hóa, hình chiếu, góc/khoảng cách qua bài mức nền.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [43], "lesson_focus": "Công thức nhân tổng quát và ôn lặp xác suất cơ bản.", "assessment_role": "practice"},
    ]},
    23: {"phase": "Bayes và sơ đồ cây song song Oxyz ứng dụng nhẹ", "lesson_focus": "Học Bayes, sơ đồ cây và giữ nhịp Oxyz bằng bài ứng dụng nhẹ.", "assessment": "weekly_quiz", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [26, 59], "lesson_focus": "Xác suất toàn phần và công thức Bayes ở mức nền.", "assessment_role": "learn"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [39, 42, 57], "lesson_focus": "Ứng dụng thực tế của mặt phẳng, vectơ và đường thẳng Oxyz mức nhẹ.", "assessment_role": "practice"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [44], "lesson_focus": "Sơ đồ cây và ôn lặp xác suất có điều kiện.", "assessment_role": "practice"},
    ]},
    24: {"phase": "Checkpoint 6: tổng nền tảng 6 tháng", "lesson_focus": "Checkpoint tổng nền tảng, đo mức sẵn sàng sang giai đoạn tổng ôn/luyện đề sau này.", "assessment": "foundation_checkpoint", "sessions": [
        {"session_no": 1, "track": "algebra_analysis_probability", "canonical_unit_ids": [1, 2, 3, 7, 8, 9, 10], "lesson_focus": "Củng cố Đại/Giải tích nền: hàm số, nguyên hàm, tích phân ứng dụng cơ bản.", "assessment_role": "checkpoint_review"},
        {"session_no": 2, "track": "geometry_oxyz", "canonical_unit_ids": [11, 12, 13, 14, 15, 16, 17, 35, 36, 37, 38, 40, 41], "lesson_focus": "Củng cố Hình/Oxyz nền: vectơ, mặt phẳng, đường thẳng, góc/khoảng cách, mặt cầu, gắn trục.", "assessment_role": "checkpoint_review"},
        {"session_no": 3, "track": "algebra_analysis_probability", "canonical_unit_ids": [23, 24, 25, 26, 43, 44, 59], "lesson_focus": "Quiz tổng nền tảng thống kê/xác suất và chữa lỗi định hướng giai đoạn sau.", "assessment_role": "checkpoint_quiz"},
    ]},
}

def foundation_week_specs() -> list[dict[str, Any]]:
    result = []
    for spec in SESSION_WEEK_SPECS:
        week = int(spec["week"])
        current = dict(spec)
        if week in FOUNDATION_WEEK_OVERRIDES:
            current.update(FOUNDATION_WEEK_OVERRIDES[week])
        result.append(current)
    return result

def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

def unique_keep_order(values: list[Any]) -> list[Any]:
    result = []
    seen = set()
    for value in values:
        if value is None or value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result

def merge_text_lists(units: list[dict[str, Any]], field: str, limit: int) -> list[str]:
    merged: list[str] = []
    seen = set()
    for unit in units:
        for value in unit.get(field, []) or []:
            text = str(value).strip()
            key = text.casefold()
            if text and key not in seen:
                merged.append(text)
                seen.add(key)
            if len(merged) >= limit:
                return merged
    return merged

def load_question_stats(mapping_path: Path) -> dict[int, dict[str, Any]]:
    data = read_json(mapping_path)
    stats: dict[int, dict[str, Any]] = defaultdict(lambda: {"question_count": 0, "review_count": 0, "difficulty_counts": Counter(), "question_type_counts": Counter()})
    for row in data.get("rows", []):
        unit_id = row.get("canonical_unit_id")
        if unit_id is None:
            continue
        current = stats[int(unit_id)]
        current["question_count"] += 1
        if row.get("mapping_needs_review") or row.get("needs_review"):
            current["review_count"] += 1
        difficulty = row.get("difficulty") or "Không rõ"
        question_type = row.get("question_type") or "unknown"
        current["difficulty_counts"][difficulty] += 1
        current["question_type_counts"][question_type] += 1
    return stats

def load_taxonomy(taxonomy: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    topics = {int(item["id"]): item for item in taxonomy.get("topics", [])}
    subtopics = {int(item["id"]): item for item in taxonomy.get("subtopics", [])}
    subtopics_by_unit: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for subtopic in taxonomy.get("subtopics", []):
        source_unit_id = subtopic.get("source_unit_id")
        if source_unit_id is not None:
            subtopics_by_unit[int(source_unit_id)].append(subtopic)
    return topics, subtopics, subtopics_by_unit

def collect_unit_ids_from_sessions(sessions: list[dict[str, Any]]) -> list[int]:
    return unique_keep_order([int(unit_id) for session in sessions for unit_id in session.get("canonical_unit_ids", [])])

def aggregate_question_stats(unit_ids: list[int], question_stats: dict[int, dict[str, Any]]) -> dict[str, Any]:
    q_count = sum(question_stats.get(unit_id, {}).get("question_count", 0) for unit_id in unit_ids)
    review_count = sum(question_stats.get(unit_id, {}).get("review_count", 0) for unit_id in unit_ids)
    difficulty_counts: Counter[str] = Counter()
    question_type_counts: Counter[str] = Counter()
    for unit_id in unit_ids:
        difficulty_counts.update(question_stats.get(unit_id, {}).get("difficulty_counts", Counter()))
        question_type_counts.update(question_stats.get(unit_id, {}).get("question_type_counts", Counter()))
    return {
        "question_count": q_count,
        "review_question_count": review_count,
        "difficulty_counts": dict(difficulty_counts),
        "question_type_counts": dict(question_type_counts),
    }

def build_pre_class_video(session: dict[str, Any], unit_ids: list[int]) -> dict[str, Any]:
    week_hint = "FOUNDATION"
    return {
        "video_id": f"MATH-{week_hint}-S{session['session_no']}-U{'-'.join(str(unit_id) for unit_id in unit_ids[:3])}",
        "title": session["lesson_focus"],
        "access_policy": "partial_free",
        "free_segment_minutes": "5-7",
        "target_total_minutes": "12-18",
        "free_segment": ["hook học để làm dạng gì", "lý thuyết cốt lõi", "dấu hiệu nhận diện dạng"],
        "locked_segment": ["ví dụ mẫu có lời giải", "lỗi sai thường gặp", "hướng dẫn bài tập trước buổi"],
        "learning_principles": ["worked_examples", "cognitive_load_control"],
    }

def build_pre_class_exercise(session: dict[str, Any]) -> dict[str, Any]:
    is_checkpoint = str(session.get("assessment_role", "")).startswith("checkpoint")
    return {
        "question_count": 5 if not is_checkpoint else 8,
        "max_difficulty": "Thông hiểu",
        "purpose": "kiểm tra đã xem video và kích hoạt nhớ lại trước buổi học",
        "access_policy": "locked_full_quiz_and_solution",
        "free_preview": "xem được yêu cầu bài tập, khóa phần làm đầy đủ và lời giải",
        "feedback_timing": "trước live class để giáo viên biết lỗi nền",
    }

def build_live_class_plan(session: dict[str, Any]) -> dict[str, Any]:
    is_checkpoint = str(session.get("assessment_role", "")).startswith("checkpoint")
    if is_checkpoint:
        segments = [
            {"minutes": 10, "activity": "retrieval warm-up từ các buổi trước"},
            {"minutes": 25, "activity": "chữa lỗi phổ biến từ pre-class exercise"},
            {"minutes": 35, "activity": "quiz/checkpoint nền tảng hoặc luyện kiểm soát"},
            {"minutes": 15, "activity": "phân tích lỗi và ghi kế hoạch bù hổng"},
            {"minutes": 5, "activity": "giao bài ôn lặp"},
        ]
    else:
        segments = [
            {"minutes": 10, "activity": "kiểm tra nhanh từ video trước buổi"},
            {"minutes": 15, "activity": "dạy lại ngắn phần lý thuyết hay sai"},
            {"minutes": 30, "activity": "dạy 1 dạng chính bằng worked example"},
            {"minutes": 25, "activity": "luyện có kiểm soát, tăng tới mức vận dụng"},
            {"minutes": 10, "activity": "mini check và chốt lỗi"},
        ]
    return {
        "duration_minutes": 90,
        "segments": segments,
        "max_difficulty": "Vận dụng",
        "cognitive_load_rule": "1 dạng chính + tối đa 1 dạng phụ",
    }

def build_post_class_homework(session: dict[str, Any]) -> dict[str, Any]:
    is_checkpoint = str(session.get("assessment_role", "")).startswith("checkpoint")
    return {
        "question_count": "8-12" if is_checkpoint else "12-18",
        "max_difficulty": "Vận dụng",
        "advanced": 0,
        "mix": ["nhận biết", "thông hiểu", "vận dụng"],
        "purpose": "củng cố sau buổi học và tạo dữ liệu lỗi cho buổi sau",
    }

def build_retrieval_review(session_no: int, unit_ids: list[int]) -> list[dict[str, Any]]:
    if session_no == 1:
        return [{"lag": "1_week", "task": "3 câu gọi lại kiến thức Đại/Giải tích/Xác suất gần nhất"}]
    if session_no == 2:
        return [{"lag": "1_week", "task": "3 câu gọi lại Hình/Oxyz từ buổi hình gần nhất"}]
    return [
        {"lag": "1_week", "task": "3 câu gọi lại kiến thức tuần trước"},
        {"lag": "2_to_3_weeks", "task": "2 câu trộn kiến thức cũ để chống quên"},
    ]

def build_session(session: dict[str, Any], question_stats: dict[int, dict[str, Any]], assessment: str) -> dict[str, Any]:
    unit_ids = [int(item) for item in session.get("canonical_unit_ids", [])]
    stats = aggregate_question_stats(unit_ids, question_stats)
    practice_policy = dict(PRACTICE_POLICY_BY_ASSESSMENT[assessment])
    if session["track"] == "geometry_oxyz":
        practice_policy = {"foundational": 10, "comprehension": 8, "application": 6, "advanced": 0}
    else:
        practice_policy = {"foundational": 10, "comprehension": 8, "application": 6, "advanced": 0}
    practice_policy["suggested_source_question_count"] = stats["question_count"]
    practice_policy["review_question_count"] = stats["review_question_count"]
    return {
        "session_no": int(session["session_no"]),
        "track": session["track"],
        "track_label": TRACK_LABELS.get(session["track"], session["track"]),
        "canonical_unit_ids": unit_ids,
        "lesson_focus": session["lesson_focus"],
        "pre_class_video": build_pre_class_video(session, unit_ids),
        "pre_class_exercise": build_pre_class_exercise(session),
        "live_class_plan": build_live_class_plan(session),
        "post_class_homework": build_post_class_homework(session),
        "retrieval_review": build_retrieval_review(int(session["session_no"]), unit_ids),
        "practice_policy": practice_policy,
        "assessment_role": session["assessment_role"],
        "question_stats": stats,
    }

def build_week(spec: dict[str, Any], units_by_id: dict[int, dict[str, Any]], topics: dict[int, dict[str, Any]], subtopics_by_unit: dict[int, list[dict[str, Any]]], question_stats: dict[int, dict[str, Any]]) -> dict[str, Any]:
    sessions = [build_session(session, question_stats, spec["assessment"]) for session in spec.get("sessions", [])]
    unit_ids = collect_unit_ids_from_sessions(sessions) if sessions else [int(item) for item in spec["canonical_unit_ids"]]
    units = [units_by_id[unit_id] for unit_id in unit_ids]
    subtopic_ids = unique_keep_order([int(sub["id"]) for unit_id in unit_ids for sub in subtopics_by_unit.get(unit_id, [])])
    topic_ids = unique_keep_order([int(sub["topic_id"]) for unit_id in unit_ids for sub in subtopics_by_unit.get(unit_id, [])])
    topic_titles = [topics[topic_id]["topic_title"] for topic_id in topic_ids if topic_id in topics]
    primary_topic_id = topic_ids[0] if topic_ids else None
    week_question_stats = aggregate_question_stats(unit_ids, question_stats)
    q_count = week_question_stats["question_count"]
    review_count = week_question_stats["review_question_count"]

    warning_flags = []
    if q_count == 0:
        warning_flags.append("missing_questions")
    if review_count:
        warning_flags.append("has_review_questions")
    if len(topic_ids) > 2 and spec["assessment"] not in {"mixed_topic_test", "full_mock_exam"}:
        warning_flags.append("wide_topic_mix")

    assessment = spec["assessment"]
    practice_policy = dict(PRACTICE_POLICY_BY_ASSESSMENT[assessment])
    practice_policy["advanced"] = 0
    practice_policy["suggested_source_question_count"] = q_count
    practice_policy["review_question_count"] = review_count

    return {
        "week": spec["week"],
        "phase": spec["phase"],
        "topic_id": primary_topic_id,
        "topic_ids": topic_ids,
        "topic_titles": topic_titles,
        "subtopic_ids": subtopic_ids,
        "canonical_unit_ids": unit_ids,
        "canonical_unit_titles": [unit["canonical_title"] for unit in units],
        "learning_objectives": merge_text_lists(units, "learning_goals", 6),
        "prerequisites": merge_text_lists(units, "prerequisites", 6),
        "lesson_focus": spec["lesson_focus"],
        "sessions": sessions,
        "practice_policy": practice_policy,
        "assessment": {"type": assessment, "label": ASSESSMENT_LABELS[assessment]},
        "teacher_notes": build_teacher_notes(spec, units, q_count, review_count, warning_flags),
        "question_stats": week_question_stats,
        "warning_flags": warning_flags,
    }

def build_teacher_notes(spec: dict[str, Any], units: list[dict[str, Any]], q_count: int, review_count: int, warning_flags: list[str]) -> list[str]:
    notes = merge_text_lists(units, "gaps_to_fill", 3)
    if q_count < 30 and spec["assessment"] not in {"full_mock_exam", "mixed_topic_test"}:
        notes.append("Kho câu hỏi gợi ý cho tuần này còn mỏng; giáo viên nên bổ sung bài tự luyện ngoài nguồn hiện có.")
    if review_count:
        notes.append(f"Có {review_count} câu hỏi/mapping cần review; ưu tiên kiểm tra trước khi giao bài số lượng lớn.")
    if "wide_topic_mix" in warning_flags:
        notes.append("Tuần này trộn nhiều topic; cần chốt lại mục tiêu buổi học để tránh dàn trải.")
    if not notes:
        notes.append("Bám sát dạng câu hỏi THPTQG, yêu cầu học sinh ghi lại lỗi sai theo nhóm kiến thức sau mỗi buổi.")
    return notes[:5]

def build_roadmap(root: Path) -> dict[str, Any]:
    canonical = read_json(root / "output_json" / "canonical_roadmap.json")
    taxonomy = read_json(root / "output_json" / "taxonomy_v2.json")
    question_stats = load_question_stats(root / "output_json" / "question_canonical_mapping.json")
    topics, _subtopics, subtopics_by_unit = load_taxonomy(taxonomy)
    units_by_id = {int(unit["order"]): unit for unit in canonical.get("roadmap_units", [])}

    weeks = [build_week(spec, units_by_id, topics, subtopics_by_unit, question_stats) for spec in foundation_week_specs()]
    coverage = compute_coverage(weeks, units_by_id)
    validation = validate_roadmap(weeks, units_by_id, taxonomy)
    return {
        "title": "Lộ trình dạy học Toán ôn thi THPTQG trong 6 tháng",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "duration_weeks": 24,
        "subject": "math",
        "exam_profile": "THPTQG",
        "summary": "Roadmap nền tảng 24 tuần có video pre-class, bài tập trước buổi, live class, BTVN và ôn lặp theo nguyên lý ghi nhớ.",
        "learning_principles": [
            "retrieval_practice",
            "spaced_repetition",
            "interleaving",
            "cognitive_load_control",
            "worked_examples",
            "immediate_feedback",
            "desirable_difficulty_without_advanced_in_foundation",
        ],
        "self_study_video_units": SELF_STUDY_VIDEO_UNITS,
        "source_paths": {
            "canonical_roadmap": str(root / "output_json" / "canonical_roadmap.json"),
            "taxonomy_v2": str(root / "output_json" / "taxonomy_v2.json"),
            "question_canonical_mapping": str(root / "output_json" / "question_canonical_mapping.json"),
            "sqlite": str(root / "output_sqlite" / "curriculum.sqlite"),
        },
        "weeks": weeks,
        "coverage": coverage,
        "validation": validation,
        "assumptions": [
            "6 tháng được chuẩn hóa thành 24 tuần học.",
            "Đối tượng chính là học sinh lớp 12 ôn thi THPTQG.",
            "Kiến thức lớp 10/11 chỉ dùng như phần tiền đề hoặc bù hổng.",
            "Phiên bản này chưa đưa HSA vào roadmap.",
            "Giai đoạn 6 tháng này là học nền tảng, chưa tổng ôn/luyện đề hoàn chỉnh.",
            "Video pre-class mở miễn phí 5-7 phút lý thuyết, khóa ví dụ mẫu/chữa bài/bài tập trước buổi.",
        ],
    }

def compute_coverage(weeks: list[dict[str, Any]], units_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    unit_weeks: dict[int, list[int]] = defaultdict(list)
    for week in weeks:
        for unit_id in week["canonical_unit_ids"]:
            unit_weeks[unit_id].append(week["week"])
    covered = sorted(unit_weeks)
    required_units = set(units_by_id) - SELF_STUDY_VIDEO_UNIT_IDS
    missing = sorted(required_units - set(covered))
    repeated = {str(unit_id): week_numbers for unit_id, week_numbers in sorted(unit_weeks.items()) if len(week_numbers) > 1}
    return {
        "canonical_unit_total": len(units_by_id),
        "required_in_class_unit_total": len(required_units),
        "covered_unit_count": len(covered),
        "covered_required_unit_count": len(set(covered) & required_units),
        "missing_unit_ids": missing,
        "self_study_video_unit_ids": sorted(SELF_STUDY_VIDEO_UNIT_IDS),
        "repeated_unit_weeks": repeated,
    }

def validate_roadmap(weeks: list[dict[str, Any]], units_by_id: dict[int, dict[str, Any]], taxonomy: dict[str, Any]) -> dict[str, Any]:
    errors = []
    warnings = []
    topic_ids = {int(item["id"]) for item in taxonomy.get("topics", [])}
    subtopic_ids = {int(item["id"]) for item in taxonomy.get("subtopics", [])}
    if len(weeks) != 24:
        errors.append(f"Expected 24 weeks, got {len(weeks)}")
    if [week["week"] for week in weeks] != list(range(1, 25)):
        errors.append("Week numbers must be contiguous from 1 to 24")
    checkpoint_weeks = {week["week"] for week in weeks if week.get("assessment", {}).get("type") == "foundation_checkpoint"}
    if checkpoint_weeks != FOUNDATION_CHECKPOINT_WEEKS:
        errors.append(f"Checkpoint weeks must be {sorted(FOUNDATION_CHECKPOINT_WEEKS)}, got {sorted(checkpoint_weeks)}")
    for week in weeks:
        if week.get("assessment", {}).get("type") in {"mixed_topic_test", "full_mock_exam"}:
            errors.append(f"Week {week['week']} still uses non-foundation assessment {week['assessment']['type']}")
        if week.get("practice_policy", {}).get("advanced", 0) > 0:
            errors.append(f"Week {week['week']} has advanced practice in foundation roadmap")
        if not week["canonical_unit_ids"] and not week.get("assessment"):
            errors.append(f"Week {week['week']} has no units or assessment")
        sessions = week.get("sessions", [])
        if len(sessions) != 3:
            errors.append(f"Week {week['week']} must have exactly 3 sessions")
        if [session.get("session_no") for session in sessions] != [1, 2, 3]:
            errors.append(f"Week {week['week']} session numbers must be [1, 2, 3]")
        track_counts = Counter(session.get("track") for session in sessions)
        if week["week"] <= 24:
            if track_counts.get("algebra_analysis_probability", 0) != 2 or track_counts.get("geometry_oxyz", 0) != 1:
                errors.append(f"Week {week['week']} must have 2 algebra/analysis/probability sessions and 1 geometry session")
        for session in sessions:
            if session.get("track") == "mixed_mock":
                errors.append(f"Week {week['week']} session {session.get('session_no')} still uses mixed_mock")
            if session.get("practice_policy", {}).get("advanced", 0) > 0:
                errors.append(f"Week {week['week']} session {session.get('session_no')} has advanced practice")
            for required_key in ["pre_class_video", "pre_class_exercise", "live_class_plan", "post_class_homework", "retrieval_review"]:
                if required_key not in session:
                    errors.append(f"Week {week['week']} session {session.get('session_no')} missing {required_key}")
            if not session.get("canonical_unit_ids"):
                errors.append(f"Week {week['week']} session {session.get('session_no')} has no canonical units")
            for unit_id in session.get("canonical_unit_ids", []):
                if unit_id not in units_by_id:
                    errors.append(f"Week {week['week']} session {session.get('session_no')} references missing canonical unit {unit_id}")
        for unit_id in week["canonical_unit_ids"]:
            if unit_id not in units_by_id:
                errors.append(f"Week {week['week']} references missing canonical unit {unit_id}")
        for topic_id in week.get("topic_ids", []):
            if topic_id not in topic_ids:
                errors.append(f"Week {week['week']} references missing topic {topic_id}")
        for subtopic_id in week.get("subtopic_ids", []):
            if subtopic_id not in subtopic_ids:
                errors.append(f"Week {week['week']} references missing subtopic {subtopic_id}")
        if week["question_stats"]["question_count"] == 0:
            warnings.append(f"Week {week['week']} has no mapped source questions")
    covered = {unit_id for week in weeks for unit_id in week["canonical_unit_ids"]}
    missing = sorted((set(units_by_id) - SELF_STUDY_VIDEO_UNIT_IDS) - covered)
    if missing:
        errors.append(f"Missing canonical unit coverage: {missing}")
    return {"status": "ok" if not errors else "failed", "errors": errors, "warnings": warnings}

def save_to_db(db_path: Path, roadmap: dict[str, Any]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute(
        """
        create table if not exists math_teaching_weeks (
            week integer primary key,
            phase text not null,
            topic_id integer,
            topic_ids_json text not null,
            subtopic_ids_json text not null,
            canonical_unit_ids_json text not null,
            learning_objectives_json text not null,
            prerequisites_json text not null,
            lesson_focus text not null,
            practice_policy_json text not null,
            assessment_json text not null,
            teacher_notes_json text not null,
            question_stats_json text not null,
            warning_flags_json text not null,
            sessions_json text not null default '[]',
            generated_at text not null
        )
        """
    )
    columns = {row[1] for row in db.execute("pragma table_info(math_teaching_weeks)")}
    if "sessions_json" not in columns:
        db.execute("alter table math_teaching_weeks add column sessions_json text not null default '[]'")
    db.execute("delete from math_teaching_weeks")
    for week in roadmap["weeks"]:
        db.execute(
            """
            insert into math_teaching_weeks (
                week, phase, topic_id, topic_ids_json, subtopic_ids_json,
                canonical_unit_ids_json, learning_objectives_json, prerequisites_json,
                lesson_focus, practice_policy_json, assessment_json, teacher_notes_json,
                question_stats_json, warning_flags_json, sessions_json, generated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                week["week"],
                week["phase"],
                week.get("topic_id"),
                json.dumps(week.get("topic_ids", []), ensure_ascii=False),
                json.dumps(week.get("subtopic_ids", []), ensure_ascii=False),
                json.dumps(week.get("canonical_unit_ids", []), ensure_ascii=False),
                json.dumps(week.get("learning_objectives", []), ensure_ascii=False),
                json.dumps(week.get("prerequisites", []), ensure_ascii=False),
                week.get("lesson_focus", ""),
                json.dumps(week.get("practice_policy", {}), ensure_ascii=False),
                json.dumps(week.get("assessment", {}), ensure_ascii=False),
                json.dumps(week.get("teacher_notes", []), ensure_ascii=False),
                json.dumps(week.get("question_stats", {}), ensure_ascii=False),
                json.dumps(week.get("warning_flags", []), ensure_ascii=False),
                json.dumps(week.get("sessions", []), ensure_ascii=False),
                roadmap["generated_at"],
            ),
        )
    db.commit()
    db.close()

def render_list(items: list[str]) -> str:
    if not items:
        return "<span class=muted>Không có</span>"
    return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"

def render_badges(items: list[Any]) -> str:
    return " ".join(f"<span class=badge>{html.escape(str(item))}</span>" for item in items)

def render_sessions(sessions: list[dict[str, Any]], compact: bool = False) -> str:
    blocks = []
    for session in sessions:
        practice = session.get("practice_policy", {})
        video = session.get("pre_class_video", {})
        pre_ex = session.get("pre_class_exercise", {})
        live = session.get("live_class_plan", {})
        homework = session.get("post_class_homework", {})
        retrieval = session.get("retrieval_review", [])
        live_segments = live.get("segments", [])
        body = (
            f"<div class=session><div class=session-title>Buổi {session['session_no']} - {html.escape(session.get('track_label', session.get('track', '')))}</div>"
            f"<p>{html.escape(session.get('lesson_focus', ''))}</p>"
            f"<p><strong>Unit:</strong> {render_badges(session.get('canonical_unit_ids', []))} "
            f"<strong>Vai trò:</strong> {html.escape(session.get('assessment_role', ''))}</p>"
        )
        if not compact:
            body += (
                f"<div class=preclass><strong>Video trước buổi:</strong> {html.escape(video.get('title', ''))} "
                f"<span class=badge>{html.escape(video.get('access_policy', ''))}</span> "
                f"<span class=badge>free {html.escape(str(video.get('free_segment_minutes', '')))} phút</span>"
                f"<p><strong>Free:</strong> {html.escape('; '.join(video.get('free_segment', [])))}.</p>"
                f"<p><strong>Khóa:</strong> {html.escape('; '.join(video.get('locked_segment', [])))}.</p></div>"
                f"<p><strong>Bài trước buổi:</strong> {pre_ex.get('question_count', 0)} câu, tối đa {html.escape(pre_ex.get('max_difficulty', ''))}; {html.escape(pre_ex.get('purpose', ''))}.</p>"
                f"<p><strong>Live 90 phút:</strong> {html.escape(' | '.join(f'{item.get('minutes')}p {item.get('activity')}' for item in live_segments))}</p>"
                f"<p><strong>Bài tập:</strong> nền tảng {practice.get('foundational', 0)}, "
                f"thông hiểu {practice.get('comprehension', 0)}, vận dụng {practice.get('application', 0)}, vận dụng cao {practice.get('advanced', 0)}; "
                f"kho gợi ý {practice.get('suggested_source_question_count', 0)} câu.</p>"
                f"<p><strong>BTVN:</strong> {html.escape(str(homework.get('question_count', '')))} câu, tối đa {html.escape(homework.get('max_difficulty', ''))}.</p>"
                f"<p><strong>Ôn lặp:</strong> {html.escape(' | '.join(item.get('task', '') for item in retrieval))}</p>"
            )
        body += "</div>"
        blocks.append(body)
    return "".join(blocks)

def render_pipeline_html(roadmap: dict[str, Any]) -> str:
    rows = []
    detail = []
    for week in roadmap["weeks"]:
        warnings = render_badges(week["warning_flags"]) if week["warning_flags"] else "<span class=muted>ok</span>"
        rows.append(
            "<tr>"
            f"<td>{week['week']}</td>"
            f"<td>{html.escape(week['phase'])}</td>"
            f"<td>{html.escape(', '.join(week['topic_titles']))}</td>"
            f"<td>{render_badges(week['canonical_unit_ids'])}</td>"
            f"<td>{week['question_stats']['question_count']}</td>"
            f"<td>{week['question_stats']['review_question_count']}</td>"
            f"<td>{html.escape(week['assessment']['label'])}</td>"
            f"<td>{warnings}</td>"
            "</tr>"
        )
        detail.append(
            f"<section><h2>Tuần {week['week']}: {html.escape(week['phase'])}</h2>"
            f"<p><strong>Trọng tâm:</strong> {html.escape(week['lesson_focus'])}</p>"
            f"<p><strong>Unit:</strong> {html.escape('; '.join(week['canonical_unit_titles']))}</p>"
            f"<p><strong>Topic IDs:</strong> {render_badges(week['topic_ids'])} <strong>Subtopic IDs:</strong> {render_badges(week['subtopic_ids'])}</p>"
            f"<p><strong>Practice:</strong> nền tảng {week['practice_policy']['foundational']}, vận dụng {week['practice_policy']['application']}, vận dụng cao {week['practice_policy']['advanced']}</p>"
            f"<h3>Phân bổ 3 buổi</h3>{render_sessions(week['sessions'])}"
            f"<h3>Mục tiêu</h3>{render_list(week['learning_objectives'])}"
            f"<h3>Tiền đề</h3>{render_list(week['prerequisites'])}"
            f"<h3>Teacher notes</h3>{render_list(week['teacher_notes'])}"
            "</section>"
        )
    coverage = roadmap["coverage"]
    validation = roadmap["validation"]
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>{html.escape(roadmap['title'])}</title>
<style>
body{{font-family:Arial,sans-serif;margin:24px;color:#172033;background:#f8fafc}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{border:1px solid #d7dee8;padding:8px;vertical-align:top}}th{{background:#1f2937;color:white;position:sticky;top:0}}section{{background:white;border:1px solid #d7dee8;margin:16px 0;padding:16px;border-radius:6px}}.metric{{display:inline-block;background:white;border:1px solid #d7dee8;padding:10px 14px;margin:0 8px 12px 0;border-radius:6px}}.metric b{{display:block;font-size:22px}}.badge{{display:inline-block;background:#e8eef7;border:1px solid #c6d2e1;padding:2px 6px;border-radius:999px;margin:1px;font-size:12px}}.muted{{color:#697386}}.session{{border:1px solid #d7dee8;background:#f8fafc;border-radius:6px;padding:10px;margin:8px 0}}.session-title{{font-weight:bold;color:#111827}}ul{{margin:6px 0 6px 18px;padding:0}}h1{{margin-bottom:8px}}h2{{margin-top:0}}
</style></head><body>
<h1>{html.escape(roadmap['title'])}</h1>
<p>{html.escape(roadmap['summary'])}</p>
<div class="metric"><b>{roadmap['duration_weeks']}</b>Tuần</div>
<div class="metric"><b>{coverage['covered_required_unit_count']}/{coverage['required_in_class_unit_total']}</b>Unit học trên lớp</div>
<div class="metric"><b>{len(coverage['self_study_video_unit_ids'])}</b>Unit video riêng</div>
<div class="metric"><b>{validation['status']}</b>Validation</div>
<h2>Tổng quan</h2><table><tr><th>Tuần</th><th>Phase</th><th>Topic</th><th>Unit IDs</th><th>Câu hỏi</th><th>Cần review</th><th>Đánh giá</th><th>Cảnh báo</th></tr>{''.join(rows)}</table>
<h2>Chi tiết tuần</h2>{''.join(detail)}
</body></html>"""

def render_teacher_html(roadmap: dict[str, Any]) -> str:
    sections = []
    for week in roadmap["weeks"]:
        sections.append(
            f"<section><div class=week>Tuần {week['week']}</div><h2>{html.escape(week['phase'])}</h2>"
            f"<p class=focus>{html.escape(week['lesson_focus'])}</p>"
            f"<h3>Phân bổ 3 buổi</h3>{render_sessions(week['sessions'])}"
            f"<h3>Nội dung dạy trong tuần</h3>{render_list(week['canonical_unit_titles'])}"
            f"<h3>Mục tiêu</h3>{render_list(week['learning_objectives'])}"
            f"<h3>Bài tập gợi ý</h3><p>Nền tảng: {week['practice_policy']['foundational']} câu; vận dụng: {week['practice_policy']['application']} câu; vận dụng cao: {week['practice_policy']['advanced']} câu. Kho hiện có gợi ý: {week['practice_policy']['suggested_source_question_count']} câu.</p>"
            f"<h3>Đánh giá</h3><p>{html.escape(week['assessment']['label'])}</p>"
            f"<h3>Lưu ý giáo viên</h3>{render_list(week['teacher_notes'])}"
            "</section>"
        )
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><title>Roadmap giáo viên - Toán THPTQG 6 tháng</title>
<style>
body{{font-family:Arial,sans-serif;margin:0;color:#1f2937;background:#ffffff}}header{{background:#f1f5f9;padding:28px 36px;border-bottom:1px solid #d7dee8}}main{{max-width:1040px;margin:0 auto;padding:24px}}section{{border-bottom:1px solid #d7dee8;padding:22px 0}}.week{{font-size:13px;text-transform:uppercase;color:#596579;font-weight:bold}}.session{{border:1px solid #d7dee8;background:#f8fafc;border-radius:6px;padding:10px;margin:8px 0}}.session-title{{font-weight:bold;color:#111827}}.badge{{display:inline-block;background:#e8eef7;border:1px solid #c6d2e1;padding:2px 6px;border-radius:999px;margin:1px;font-size:12px}}h1,h2,h3{{margin:0 0 8px}}h3{{font-size:15px;margin-top:14px}}.focus{{font-size:16px;line-height:1.5}}ul{{margin:6px 0 6px 20px;padding:0}}li{{margin:3px 0}}
</style></head><body><header><h1>Roadmap giáo viên - Toán THPTQG 6 tháng</h1><p>24 tuần học, không bao gồm HSA. Sinh lúc {html.escape(roadmap['generated_at'])}.</p></header><main>{''.join(sections)}</main></body></html>"""

def run(root: Path, validate_only: bool = False) -> dict[str, Any]:
    roadmap = build_roadmap(root)
    if validate_only:
        return roadmap
    write_json(root / "output_json" / "math_teaching_roadmap.json", roadmap)
    save_to_db(root / "output_sqlite" / "curriculum.sqlite", roadmap)
    write_text(root / "previews" / "math_teaching_roadmap.html", render_pipeline_html(roadmap))
    write_text(root / "previews" / "math_teacher_roadmap.html", render_teacher_html(roadmap))
    return roadmap

def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Build a 24-week THPTQG math teaching roadmap from local curriculum artifacts.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Local curriculum root folder.")
    parser.add_argument("--validate-only", action="store_true", help="Build and validate in memory without writing outputs.")
    args = parser.parse_args()
    roadmap = run(Path(args.root), validate_only=args.validate_only)
    validation = roadmap["validation"]
    print(f"Validation: {validation['status']}")
    print(f"Weeks: {len(roadmap['weeks'])}")
    coverage = roadmap["coverage"]
    print(f"Coverage: {coverage['covered_required_unit_count']}/{coverage['required_in_class_unit_total']} in-class units; {len(coverage['self_study_video_unit_ids'])} video units")
    if validation["errors"]:
        for error in validation["errors"]:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    for warning in validation["warnings"]:
        print(f"WARN: {warning}")
    if not args.validate_only:
        root = Path(args.root)
        print(f"Wrote {(root / 'output_json' / 'math_teaching_roadmap.json').resolve()}")
        print(f"Wrote {(root / 'previews' / 'math_teaching_roadmap.html').resolve()}")
        print(f"Wrote {(root / 'previews' / 'math_teacher_roadmap.html').resolve()}")

if __name__ == "__main__":
    main()
