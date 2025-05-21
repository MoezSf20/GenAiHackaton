from flask import Flask, render_template, jsonify, request, session
import json
import os
from huggingface_hub import InferenceClient
from datetime import datetime
from collections import Counter, defaultdict
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize the client with your API key
client = InferenceClient(
    provider="hyperbolic",
    api_key=os.getenv("apiKey") # In order to have this working you need a variable called apiKey=""
    # example: export apiKey="<your-api-key>"
)

# JSON file to store problem-solution pairs
JSON_FILE = "problems.json"

# System prompt for the chatbot
SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are an AI assistant for a company that offers services like consulting, software development, customer support, and training. "
        "Use the provided problem-solution data to answer client questions accurately. If a question matches a stored problem, provide the corresponding solution. "
        "For general questions, respond professionally about company services. If the question is unrelated, politely steer the conversation back to company services."
        "Keep the answers relatively short."
    )
}

# Initialize conversation history
conversation_history = [SYSTEM_PROMPT]

def load_problems():
    """Load problem-solution pairs from the JSON file."""
    logger.debug(f"Attempting to load problems from {JSON_FILE}")
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r') as f:
                problems = json.load(f)
                logger.debug(f"Successfully loaded {len(problems)} problems")
                return problems
        except json.JSONDecodeError as e:
            logger.error(f"Error: Invalid JSON format in {JSON_FILE}: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Error loading problems: {str(e)}")
            return []
    logger.debug(f"File {JSON_FILE} does not exist, returning empty list")
    return []

def save_problems(problems):
    """Save problem-solution pairs to the JSON file."""
    logger.debug(f"Attempting to save {len(problems)} problems to {JSON_FILE}")
    try:
        with open(JSON_FILE, 'w') as f:
            json.dump(problems, f, indent=4)
        logger.debug("Successfully saved problems")
    except Exception as e:
        logger.error(f"Error saving to JSON file: {str(e)}")
        raise

def get_current_timestamp():
    """Get current timestamp in ISO format."""
    return datetime.now().isoformat()

def find_similar_problem(problems, new_problem):
    """Find similar problems in the database using basic text similarity."""
    new_problem_lower = new_problem.lower()
    for problem in problems:
        if problem['problem'].lower() in new_problem_lower or new_problem_lower in problem['problem'].lower():
            return problem
    return None

def update_problem_metadata(problem):
    """Update metadata for an existing problem."""
    problem['metadata']['occurrences'] += 1
    problem['metadata']['last_seen'] = get_current_timestamp()

def create_new_problem_entry(problem, solution, category="technical", severity="medium"):
    """Create a new problem entry with metadata."""
    return {
        "problem": problem,
        "solution": solution,
        "metadata": {
            "occurrences": 1,
            "first_seen": get_current_timestamp(),
            "last_seen": get_current_timestamp(),
            "category": category,
            "severity": severity
        }
    }

def analyze_problems(problems):
    """Analyze problems and generate insights."""
    if not problems:
        return {
            "total_problems": 0,
            "total_occurrences": 0,
            "categories": {},
            "severities": {},
            "recent_problems": 0
        }

    # Basic statistics
    total_problems = len(problems)
    total_occurrences = sum(p['metadata']['occurrences'] for p in problems)
    
    # Category analysis
    category_stats = defaultdict(lambda: {'count': 0, 'problems': [], 'occurrences': 0})
    severity_stats = defaultdict(int)
    
    for problem in problems:
        category = problem['metadata']['category']
        occurrences = problem['metadata']['occurrences']
        
        category_stats[category]['count'] += 1
        category_stats[category]['occurrences'] += occurrences
        category_stats[category]['problems'].append(problem)
        severity_stats[problem['metadata']['severity']] += occurrences

    # Time-based analysis
    recent_problems = [p for p in problems if (datetime.now() - datetime.fromisoformat(p['metadata']['last_seen'])).days <= 7]
    
    return {
        "total_problems": total_problems,
        "total_occurrences": total_occurrences,
        "categories": dict(category_stats),
        "severities": dict(severity_stats),
        "recent_problems": len(recent_problems)
    }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    user_input = data.get('message', '').strip()
    
    if not user_input:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    # Add user message to history
    conversation_history.append({"role": "user", "content": user_input})
    
    # Prepare context from JSON file
    problems = load_problems()
    problems_context = "Stored Problem-Solution Data:\n"
    for entry in problems:
        problems_context += f"Problem: {entry['problem']}\nSolution: {entry['solution']}\n\n"
    
    # Combine system prompt with problems context
    full_system_prompt = (
        SYSTEM_PROMPT["content"] + "\n\n" + problems_context
    )
    conversation_history[0] = {"role": "system", "content": full_system_prompt}
    
    try:
        # Call the Hugging Face API
        completion = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct",
            messages=conversation_history,
            max_tokens=150,
            temperature=0.7,
            top_p=0.9,
        )
        
        # Get AI response
        ai_response = completion.choices[0].message.content
        
        # Add AI response to history
        conversation_history.append({"role": "assistant", "content": ai_response})
        
        # Optional: Limit history to prevent excessive growth
        if len(conversation_history) > 10:
            conversation_history[:] = [conversation_history[0]] + conversation_history[-9:]
        
        return jsonify({'response': ai_response})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/problems', methods=['GET'])
def get_problems():
    problems = load_problems()
    return jsonify(problems)

@app.route('/problems', methods=['POST'])
def add_problem():
    logger.debug("Received POST request to /problems")
    try:
        data = request.get_json()
        logger.debug(f"Received data: {data}")
        
        if not data:
            logger.error("No data provided in request")
            return jsonify({'error': 'No data provided'}), 400

        problem = data.get('problem', '').strip()
        solution = data.get('solution', '').strip()
        category = data.get('category', 'technical')
        severity = data.get('severity', 'medium')
        
        logger.debug(f"Processed data - Problem: {problem}, Solution: {solution}, Category: {category}, Severity: {severity}")
        
        if not problem or not solution:
            logger.error("Empty problem or solution")
            return jsonify({'error': 'Problem and solution cannot be empty'}), 400
        
        problems = load_problems()
        logger.debug(f"Loaded {len(problems)} existing problems")
        
        # Check for similar problems
        similar_problem = find_similar_problem(problems, problem)
        if similar_problem:
            logger.debug(f"Found similar problem: {similar_problem['problem']}")
            update_problem_metadata(similar_problem)
            save_problems(problems)
            return jsonify({
                'message': 'Similar problem found and updated',
                'problem': similar_problem
            })
        
        # Create new problem entry
        new_problem = create_new_problem_entry(problem, solution, category, severity)
        logger.debug(f"Created new problem entry: {new_problem}")
        problems.append(new_problem)
        save_problems(problems)
        
        return jsonify({
            'message': 'Problem added successfully',
            'problem': new_problem
        })
    except Exception as e:
        logger.error(f"Error adding problem: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/analysis', methods=['GET'])
def get_analysis():
    problems = load_problems()
    analysis = analyze_problems(problems)
    return jsonify(analysis)

@app.route('/problems', methods=['DELETE'])
def clear_problems():
    save_problems([])
    return jsonify({'message': 'All problems cleared successfully'})

if __name__ == '__main__':
    # Initialize JSON file if it doesn't exist
    if not os.path.exists(JSON_FILE):
        logger.info(f"Creating new {JSON_FILE} file")
        save_problems([])
    app.run(debug=True)
