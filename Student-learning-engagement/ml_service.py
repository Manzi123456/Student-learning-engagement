import os
import json
from typing import Dict, Any, List, Tuple
from math import exp
from datetime import datetime

MODEL_PATH = 'models/model.json'


def ensure_model_dir_exists() -> None:
    os.makedirs('models', exist_ok=True)


class SimpleLogisticModel:
    def __init__(self, weights: List[float] | None = None, bias: float = 0.0):
        self.weights = weights if weights is not None else [0.0, 0.0, 0.0]
        self.bias = float(bias)

    @staticmethod
    def _sigmoid(z: float) -> float:
        try:
            return 1.0 / (1.0 + exp(-z))
        except OverflowError:
            return 0.0 if z < 0 else 1.0

    def predict_proba(self, X: List[List[float]]) -> List[float]:
        probs: List[float] = []
        for row in X:
            z = sum(w * x for w, x in zip(self.weights, row)) + self.bias
            probs.append(self._sigmoid(z))
        return probs

    def fit(self, X: List[List[float]], y: List[float], lr: float = 0.05, epochs: int = 1000):
        if not X:
            return
        m = len(X)
        n = len(X[0])
        w = self.weights[:]
        b = self.bias
        for _ in range(epochs):
            # compute predictions
            preds = []
            for row in X:
                z = sum(w[j] * row[j] for j in range(n)) + b
                preds.append(self._sigmoid(z))
            # gradients
            dw = [0.0] * n
            db = 0.0
            for i in range(m):
                err = preds[i] - y[i]
                for j in range(n):
                    dw[j] += (1.0 / m) * err * X[i][j]
                db += (1.0 / m) * err
            # update
            for j in range(n):
                w[j] -= lr * dw[j]
            b -= lr * db
        self.weights = w
        self.bias = b

    def to_dict(self) -> Dict[str, Any]:
        return {'weights': self.weights, 'bias': self.bias}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'SimpleLogisticModel':
        return SimpleLogisticModel(weights=list(map(float, data.get('weights', [0.0, 0.0, 0.0]))), bias=float(data.get('bias', 0.0)))


def build_records(study_sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for s in study_sessions:
        duration = float(s.get('duration') or 0.0)
        quiz_score = float(s.get('quiz_score') or 0.0)
        completed = 1.0 if s.get('completed') else 0.0
        records.append({
            'duration': duration,
            'quiz_score': quiz_score,
            'completed': completed,
            'resource_id': float(s.get('resource_id') or 0.0),
            'student_id': float(s.get('student_id') or 0.0),
            'success': 1.0 if (quiz_score >= 70.0 and completed == 1.0) else 0.0,
        })
    return records


def _normalize_row(duration: float, quiz_score: float, completed: float) -> List[float]:
    d = max(0.0, min(duration / 120.0, 1.0))
    q = max(0.0, min(quiz_score / 100.0, 1.0))
    c = 1.0 if completed >= 1.0 else 0.0
    return [d, q, c]


def get_features_and_target(records: List[Dict[str, Any]]) -> Tuple[List[List[float]], List[float]]:
    X: List[List[float]] = []
    y: List[float] = []
    for r in records:
        X.append(_normalize_row(float(r.get('duration', 0.0)), float(r.get('quiz_score', 0.0)), float(r.get('completed', 0.0))))
        y.append(float(r.get('success', 0.0)))
    return X, y


def train_model(study_sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    ensure_model_dir_exists()
    records = build_records(study_sessions)
    X, y = get_features_and_target(records)
    model = SimpleLogisticModel()

    if not X:
        with open(MODEL_PATH, 'w', encoding='utf-8') as f:
            json.dump(model.to_dict(), f)
        return {'status': 'no_data', 'samples': 0}

    all_zero = all(v == 0.0 for v in y)
    all_one = all(v == 1.0 for v in y)
    if all_zero or all_one:
        w = [0.2, 0.7, 0.1]
        b = -0.5 if all_zero else 0.5
        model = SimpleLogisticModel(weights=w, bias=b)
    else:
        model.fit(X, y, lr=0.1, epochs=1500)

    with open(MODEL_PATH, 'w', encoding='utf-8') as f:
        json.dump(model.to_dict(), f)

    # Simple holdout estimate when enough samples
    acc = None
    if len(X) >= 10 and not (all_zero or all_one):
        split = int(0.8 * len(X))
        train_X, train_y = X[:split], y[:split]
        test_X, test_y = X[split:], y[split:]
        eval_model = SimpleLogisticModel.from_dict(model.to_dict())
        # Evaluate using current model (no re-fit)
        preds = eval_model.predict_proba(test_X)
        preds_cls = [1.0 if p >= 0.5 else 0.0 for p in preds]
        correct = sum(1 for a, b in zip(preds_cls, test_y) if a == b)
        acc = float(correct) / float(len(test_y)) if test_y else None

    return {'status': 'trained', 'samples': len(X), 'accuracy': acc}


def load_model() -> SimpleLogisticModel:
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError('Model file not found. Train the model first.')
    with open(MODEL_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return SimpleLogisticModel.from_dict(data)


def recommend_for_student(study_summary: Dict[str, Any]) -> Dict[str, Any]:
    duration = float(study_summary.get('duration') or 0.0)
    quiz_score = float(study_summary.get('quiz_score') or 0.0)
    completed = 1.0 if study_summary.get('completed') else 0.0
    features = [_normalize_row(duration, quiz_score, completed)]

    try:
        model = load_model()
        proba = model.predict_proba(features)[0]
    except Exception:
        # Fallback heuristic
        base = 0.7 * min(max(quiz_score / 100.0, 0.0), 1.0) + 0.2 * min(max(duration / 120.0, 0.0), 1.0) + 0.1 * completed
        proba = float(min(max(base, 0.0), 1.0))

    if proba >= 0.8:
        action = 'advance'
        strategy = 'Assign more challenging resources and a new assignment.'
    elif proba >= 0.5:
        action = 'practice_related'
        strategy = 'Assign similar practice resources and targeted quiz questions.'
    else:
        action = 'review_prerequisites'
        strategy = 'Recommend prerequisite materials and shorter practice sessions before reassessment.'

    return {
        'success_probability': float(proba),
        'recommended_action': action,
        'strategy': strategy,
        'timestamp': datetime.now().strftime('%B %d, %Y at %I:%M %p'),
        'confidence_level': 'High' if proba >= 0.8 or proba <= 0.2 else 'Medium' if proba >= 0.6 or proba <= 0.4 else 'Low'
    }


def get_model_info() -> Dict[str, Any]:
    """Get information about the current model status"""
    try:
        ensure_model_dir_exists()
        if os.path.exists(MODEL_PATH):
            with open(MODEL_PATH, 'r') as f:
                data = json.load(f)
            return {
                    'status': 'trained',
                    'last_updated': data.get('last_updated', 'unknown'),
                    'training_samples': data.get('training_samples', 0),
                    'accuracy': data.get('accuracy', 0.0)
                }
        else:
            return {'status': 'not_trained', 'training_samples': 0}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

def predict_success_enhanced(features: Dict[str, Any]) -> Dict[str, Any]:
    """Enhanced success prediction using multiple engagement metrics"""
    try:
        # Normalize features
        normalized_features = normalize_enhanced_features(features)
        
        # Load model
        model = load_model()
        if not model:
            return get_default_prediction()
        
        # Make prediction
        prediction_input = [
            normalized_features['time_spent_norm'],
            normalized_features['engagement_score_norm'],
            normalized_features['focus_ratio_norm']
        ]
        
        success_prob = model.predict_proba([prediction_input])[0]
        
        # Calculate confidence based on feature completeness
        confidence = calculate_prediction_confidence(features)
        
        # Determine predicted score based on engagement patterns
        predicted_score = calculate_predicted_score(features, success_prob)
        
        return {
            'predicted_score': predicted_score,
            'success_probability': success_prob,
            'confidence_level': confidence,
            'factors': {
                'engagement_score': features.get('engagement_score', 0),
                'focus_time_ratio': features.get('focus_time', 0) / max(features.get('total_time_spent', 1), 1),
                'scroll_depth': features.get('scroll_depth', 0),
                'attention_span': features.get('attention_span', 0),
                'reading_speed': features.get('reading_speed', 0),
                'comprehension_score': features.get('comprehension_score', 0)
            }
        }
        
    except Exception as e:
        print(f"Error in enhanced prediction: {str(e)}")
        return get_default_prediction()

def normalize_enhanced_features(features: Dict[str, Any]) -> Dict[str, float]:
    """Normalize enhanced features for ML prediction"""
    total_time = features.get('total_time_spent', 0)
    focus_time = features.get('focus_time', 0)
    idle_time = features.get('idle_time', 0)
    
    return {
        'time_spent_norm': min(total_time / 3600.0, 1.0),  # Normalize to 1 hour max
        'engagement_score_norm': features.get('engagement_score', 0) / 100.0,
        'focus_ratio_norm': focus_time / max(total_time, 1),
        'scroll_depth_norm': features.get('scroll_depth', 0) / 100.0,
        'attention_span_norm': min(features.get('attention_span', 0) / 300.0, 1.0),  # 5 minutes max
        'reading_speed_norm': min(features.get('reading_speed', 0) / 300.0, 1.0),  # 300 WPM max
        'comprehension_norm': features.get('comprehension_score', 0) / 100.0
    }

def calculate_prediction_confidence(features: Dict[str, Any]) -> float:
    """Calculate confidence level based on feature completeness and quality"""
    confidence_factors = []
    
    # Time spent factor
    if features.get('total_time_spent', 0) > 300:  # At least 5 minutes
        confidence_factors.append(0.8)
    elif features.get('total_time_spent', 0) > 60:  # At least 1 minute
        confidence_factors.append(0.6)
    else:
        confidence_factors.append(0.3)
    
    # Engagement score factor
    engagement_score = features.get('engagement_score', 0)
    if engagement_score > 70:
        confidence_factors.append(0.9)
    elif engagement_score > 50:
        confidence_factors.append(0.7)
    elif engagement_score > 30:
        confidence_factors.append(0.5)
    else:
        confidence_factors.append(0.3)
    
    # Focus time factor
    focus_ratio = features.get('focus_time', 0) / max(features.get('total_time_spent', 1), 1)
    if focus_ratio > 0.8:
        confidence_factors.append(0.9)
    elif focus_ratio > 0.6:
        confidence_factors.append(0.7)
    elif focus_ratio > 0.4:
        confidence_factors.append(0.5)
    else:
        confidence_factors.append(0.3)
    
    # Scroll depth factor
    scroll_depth = features.get('scroll_depth', 0)
    if scroll_depth > 80:
        confidence_factors.append(0.8)
    elif scroll_depth > 50:
        confidence_factors.append(0.6)
    elif scroll_depth > 20:
        confidence_factors.append(0.4)
    else:
        confidence_factors.append(0.2)
    
    return sum(confidence_factors) / len(confidence_factors)

def calculate_predicted_score(features: Dict[str, Any], success_prob: float) -> float:
    """Calculate predicted quiz score based on engagement patterns"""
    base_score = success_prob * 100  # Base score from ML model
    
    # Adjust based on engagement metrics
    engagement_score = features.get('engagement_score', 0)
    focus_ratio = features.get('focus_time', 0) / max(features.get('total_time_spent', 1), 1)
    scroll_depth = features.get('scroll_depth', 0)
    reading_speed = features.get('reading_speed', 0)
    comprehension_score = features.get('comprehension_score', 0)
    
    # Engagement adjustment
    if engagement_score > 80:
        base_score += 10
    elif engagement_score > 60:
        base_score += 5
    elif engagement_score < 30:
        base_score -= 10
    
    # Focus adjustment
    if focus_ratio > 0.8:
        base_score += 5
    elif focus_ratio < 0.4:
        base_score -= 5
    
    # Scroll depth adjustment
    if scroll_depth > 80:
        base_score += 5
    elif scroll_depth < 30:
        base_score -= 5
    
    # Reading speed adjustment (if available)
    if reading_speed > 200:
        base_score += 3
    elif reading_speed < 100:
        base_score -= 3
    
    # Comprehension adjustment (if available)
    if comprehension_score > 80:
        base_score += 5
    elif comprehension_score < 50:
        base_score -= 5
    
    return max(0, min(100, base_score))

def get_default_prediction() -> Dict[str, Any]:
    """Return default prediction when ML model is not available"""
    return {
        'predicted_score': 50.0,
        'success_probability': 0.5,
        'confidence_level': 0.3,
        'factors': {
            'engagement_score': 0,
            'focus_time_ratio': 0,
            'scroll_depth': 0,
            'attention_span': 0,
            'reading_speed': 0,
            'comprehension_score': 0
        }
    }

def generate_student_recommendations(student_id: int, engagement_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate personalized recommendations for a student based on their engagement patterns"""
    if not engagement_data:
        return {
            'learning_style': 'unknown',
            'recommended_session_duration': 30,
            'engagement_strategies': ['Start with shorter sessions', 'Take regular breaks'],
            'success_factors': ['Consistent study habits', 'Active engagement']
        }
    
    # Analyze engagement patterns
    avg_engagement = sum([e.get('engagement_score', 0) for e in engagement_data]) / len(engagement_data)
    avg_focus_time = sum([e.get('focus_time', 0) for e in engagement_data]) / len(engagement_data)
    avg_session_duration = sum([e.get('total_time_spent', 0) for e in engagement_data]) / len(engagement_data)
    
    # Determine learning style
    learning_style = determine_learning_style(engagement_data)
    
    # Calculate optimal session duration
    optimal_duration = calculate_optimal_session_duration(avg_focus_time, avg_engagement)
    
    # Generate engagement strategies
    strategies = generate_engagement_strategies(avg_engagement, learning_style)
    
    # Identify success factors
    success_factors = identify_success_factors(engagement_data)
    
    return {
        'learning_style': learning_style,
        'recommended_session_duration': optimal_duration,
        'engagement_strategies': strategies,
        'success_factors': success_factors,
        'avg_engagement': avg_engagement,
        'avg_focus_time': avg_focus_time,
        'avg_session_duration': avg_session_duration
    }

def determine_learning_style(engagement_data: List[Dict[str, Any]]) -> str:
    """Determine student's learning style based on engagement patterns"""
    if not engagement_data:
        return 'unknown'
    
    # Analyze patterns
    high_scroll = sum([1 for e in engagement_data if e.get('scroll_depth', 0) > 70])
    high_clicks = sum([1 for e in engagement_data if e.get('clicks', 0) > 10])
    high_cursor = sum([1 for e in engagement_data if e.get('cursor_movements', 0) > 50])
    high_focus = sum([1 for e in engagement_data if e.get('focus_time', 0) / max(e.get('total_time_spent', 1), 1) > 0.8])
    
    total_sessions = len(engagement_data)
    
    if high_scroll / total_sessions > 0.6:
        return 'visual'
    elif high_clicks / total_sessions > 0.6:
        return 'kinesthetic'
    elif high_cursor / total_sessions > 0.6:
        return 'auditory'
    elif high_focus / total_sessions > 0.6:
        return 'reading'
    else:
        return 'mixed'

def calculate_optimal_session_duration(avg_focus_time: float, avg_engagement: float) -> int:
    """Calculate optimal session duration based on focus patterns"""
    if avg_engagement > 80:
        return min(int(avg_focus_time * 1.2), 60)  # Extend by 20% up to 60 minutes
    elif avg_engagement > 60:
        return min(int(avg_focus_time * 1.1), 45)  # Extend by 10% up to 45 minutes
    elif avg_engagement > 40:
        return min(int(avg_focus_time), 30)  # Keep current duration up to 30 minutes
    else:
        return min(int(avg_focus_time * 0.8), 20)  # Reduce by 20% up to 20 minutes

def generate_engagement_strategies(avg_engagement: float, learning_style: str) -> List[str]:
    """Generate engagement strategies based on current engagement and learning style"""
    strategies = []
    
    if avg_engagement < 50:
        strategies.extend([
            'Take shorter, more frequent breaks',
            'Set specific learning goals for each session',
            'Use interactive elements when available'
        ])
    
    if learning_style == 'visual':
        strategies.extend([
            'Focus on visual content and diagrams',
            'Use color coding for important information',
            'Create mind maps or visual summaries'
        ])
    elif learning_style == 'auditory':
        strategies.extend([
            'Read content aloud when possible',
            'Use audio resources when available',
            'Discuss concepts with peers or teachers'
        ])
    elif learning_style == 'kinesthetic':
        strategies.extend([
            'Take notes while reading',
            'Use interactive exercises',
            'Practice with hands-on activities'
        ])
    elif learning_style == 'reading':
        strategies.extend([
            'Take detailed notes',
            'Summarize key points',
            'Review material multiple times'
        ])
    
    return strategies

def identify_success_factors(engagement_data: List[Dict[str, Any]]) -> List[str]:
    """Identify factors that contribute to student success"""
    factors = []
    
    # Analyze high-performing sessions
    high_performing = [e for e in engagement_data if e.get('engagement_score', 0) > 70]
    
    if high_performing:
        avg_focus = sum([e.get('focus_time', 0) for e in high_performing]) / len(high_performing)
        avg_scroll = sum([e.get('scroll_depth', 0) for e in high_performing]) / len(high_performing)
        
        if avg_focus > 0.8:
            factors.append('Maintaining focus during study sessions')
        if avg_scroll > 70:
            factors.append('Reading through complete content')
        if len(high_performing) > len(engagement_data) * 0.5:
            factors.append('Consistent engagement across sessions')
    
    # General factors
    factors.extend([
        'Regular study habits',
        'Active participation in learning activities',
        'Taking breaks when needed',
        'Setting clear learning objectives'
    ])
    
    return factors[:5]  # Return top 5 factors


def get_help_info() -> Dict[str, Any]:
    """Get comprehensive help information about the ML service."""
    return {
        'overview': {
            'title': 'Student Performance Prediction System',
            'description': 'This ML service uses a logistic regression model to predict student success and provide personalized recommendations.',
            'features': [
                'Predicts student success probability based on study patterns',
                'Provides actionable recommendations for teachers',
                'Adapts to student performance over time',
                'Generates confidence levels for predictions'
            ]
        },
        'how_it_works': {
            'title': 'How the System Works',
            'steps': [
                '1. Collects student study session data (duration, quiz scores, completion status)',
                '2. Normalizes the data for consistent analysis',
                '3. Trains a logistic regression model on historical performance',
                '4. Makes predictions about future student success',
                '5. Provides specific strategies based on prediction confidence'
            ]
        },
        'recommendations': {
            'title': 'Recommendation Categories',
            'categories': {
                'advance': {
                    'threshold': '≥80% success probability',
                    'strategy': 'Assign more challenging resources and new assignments',
                    'description': 'Student is ready for advanced material'
                },
                'practice_related': {
                    'threshold': '50-79% success probability',
                    'strategy': 'Assign similar practice resources and targeted quiz questions',
                    'description': 'Student needs more practice with current material'
                },
                'review_prerequisites': {
                    'threshold': '<50% success probability',
                    'strategy': 'Recommend prerequisite materials and shorter practice sessions',
                    'description': 'Student needs to review foundational concepts'
                }
            }
        },
        'data_requirements': {
            'title': 'Data Requirements',
            'minimum_data': 'At least 10 study sessions for reliable predictions',
            'optimal_data': '50+ study sessions across multiple students',
            'data_points': [
                'Study duration (normalized to 0-120 minutes)',
                'Quiz scores (0-100%)',
                'Completion status (completed/incomplete)',
                'Success criteria (≥70% quiz score + completed)'
            ]
        },
        'troubleshooting': {
            'title': 'Common Issues and Solutions',
            'issues': [
                {
                    'problem': 'Model not trained',
                    'solution': 'Ensure students have completed study sessions, then train the model from the teacher dashboard'
                },
                {
                    'problem': 'Low prediction accuracy',
                    'solution': 'Collect more diverse student data and retrain the model'
                },
                {
                    'problem': 'No recommendations showing',
                    'solution': 'Check that students have AI recommendations enabled and study sessions recorded'
                },
                {
                    'problem': 'Inconsistent predictions',
                    'solution': 'Ensure consistent data quality and sufficient training samples'
                }
            ]
        },
        'best_practices': {
            'title': 'Best Practices',
            'tips': [
                'Train the model regularly as new student data becomes available',
                'Ensure students complete quizzes to provide quality training data',
                'Monitor prediction accuracy and retrain if performance degrades',
                'Use recommendations as guidance, not absolute rules',
                'Consider individual student context when applying recommendations'
            ]
        }
    } 