import os
import pdfplumber    # For extracting text from PDF
import streamlit as st   # For building the web interface
from sentence_transformers import SentenceTransformer   # For generating embeddings
from pinecone import Pinecone, ServerlessSpec   # For connecting to Pinecone vector DB
import cohere   # For generating answers using Cohere's API
import tempfile  # For creating temporary files

# Setting a custom theme using Streamlit's built-in features
st.set_page_config(page_title="🤖 Interactive QA Bot")

# Adding CSS styles for better appearance
st.markdown(
    '''
    <style>
    .main {
        background-color: #f0f4f8;  /* Light background */
    }
    h1 {
        color: #2a2a72; /* Darker color for headings */
    }
    .stTextInput, .stButton {
        background-color: #ffffff; /* White for input and buttons */
        border: 1px solid #2a2a72; /* Darker border */
    }
    .stTextInput:focus {
        border-color: #9a1750; /* Accent color on focus */
    }
    .stButton:hover {
        background-color: #2a2a72; /* Hover effect for buttons */
        color: white; /* White text on hover */
    }
    </style>
    ''',
    unsafe_allow_html=True
)

# Initializing the SentenceTransformer model for embedding generation
embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

# API key for Cohere
cohere_api_key = os.getenv('COHERE_API_KEY')
co = cohere.Client(cohere_api_key)   # Initializing Cohere client

# Initializing Pinecone
pinecone_api_key = os.getenv('PINECONE_API_KEY')
pc = Pinecone(api_key=pinecone_api_key)

# Defining the index name and embedding dimensions
index_name = "qa-bot-index"
dimension = 384   # The dimension of embeddings generated by 'all-MiniLM-L6-v2'

# Creating the index if it doesn't already exist
if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=dimension,
        metric='cosine',   # Using cosine similarity for similarity search
        spec=ServerlessSpec(cloud='aws', region='us-east-1')   # cloud and region for the index
    )

# Connecting to the Pinecone index
index = pc.Index(index_name)

# Function to extract text from PDF document
def extract_text_from_pdf(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        text = ""
        # Loop through all the pages and extract text
        for page in pdf.pages:
            text += page.extract_text() if page.extract_text() else ""
    return text

# Function to clear previous embeddings from Pinecone
def clear_previous_embeddings():
  # Retrieve all existing vectors in the index
    # Use a query to get the IDs of all vectors
    response = index.query(vector=[0] * dimension, top_k=1000)  # Dummy vector, adjust top_k as needed
    ids_to_delete = [match['id'] for match in response['matches']]

    if ids_to_delete:
        index.delete(ids=ids_to_delete)  # Clear all previous embeddings in the index
    
# Function to split text into smaller chunks for embedding
def chunk_text(text, chunk_size=200):
    sentences = text.split('. ')   # Splitting text by sentence
    chunks = []
    current_chunk = []
    current_length = 0

    # Grouping sentences until the chunk size is reached
    for sentence in sentences:
        sentence_length = len(sentence.split())
        if current_length + sentence_length <= chunk_size:
            current_chunk.append(sentence)
            current_length += sentence_length
        else:
            chunks.append(' '.join(current_chunk)) # Adding the current chunk
            current_chunk = [sentence]   # Starting a new chunk
            current_length = sentence_length

    if current_chunk:  # Add the last chunk if it exists
        chunks.append(' '.join(current_chunk))

    return chunks   # Return the list of chunks

# Function to upload embeddings and text to Pinecone for storage
def upload_embeddings_to_pinecone(embeddings, texts):
    # Pairing each embedding with its respective text, assigning unique IDs
    vectors = [(f"id_{i}", embedding, {"text": text}) for i, (embedding, text) in enumerate(zip(embeddings, texts))]
    index.upsert(vectors)   # Uploading vectors to Pinecone

# Function to retrieve the most relevant chunks from Pinecone
def retrieve_relevant_chunks(query, top_k=5):
    query_embedding = embedding_model.encode(query).tolist()   # Generate query embedding
    # Searching Pinecone index for the top_k most similar chunks
    results = index.query(vector=query_embedding, top_k=top_k, include_metadata=True)
    return results

# Function to generate an answer based on retrieved text chunks and the query
def generate_answer(retrieved_texts, query):
    # Concatenating the relevant retrieved texts
    context = "\n".join([match['metadata']['text'] for match in retrieved_texts['matches']])

    # Limit the context to a maximum length for the generative model (cohere)
    context_limit = 1000
    if len(context) > context_limit:
        context = context[:context_limit] + "... (truncated)"   # Truncate long contexts

    # Creating a prompt for the generative model
    prompt = (
        f"Based on the context provided below, please summarize the document in a concise manner:\n\n"
        f"Context: {context}\n\n"
        f"Question: {query}\n\n"
        f"Answer:"
    )

    # Call Cohere's generate method to produce an answer based on the prompt
    response = co.generate(
        model='command-r-plus',   # Using the command model for strong reasoning and QA
        prompt=prompt,
        max_tokens=150,   # Setting a token limit for the response
        temperature=0.5,   # Controls randomness of the answer (higher = more creative)
        stop_sequences=["Answer:"]
    )
    answer = response.generations[0].text.strip()    # Extract and return the generated answer
    return answer

# Streamlit app logic
def start_streamlit():
    st.title("🤖 Interactive QA Bot")   # Set the title of the Streamlit app

    # Initializing chat history in session state if it doesn't exist
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    # Initializing query_input in session state if it doesn't exist
    if "query_input" not in st.session_state:
        st.session_state["query_input"] = ""

    # Allow users to upload a PDF document
    uploaded_file = st.file_uploader("📥 Upload a PDF document", type="pdf")

    if uploaded_file is not None:
        # Save the uploaded file in a temporary directory
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(uploaded_file.read())
            pdf_path = temp_file.name  # Get the path of the temp file

        clear_previous_embeddings()
        
        # Extract text from PDF
        document_text = extract_text_from_pdf(pdf_path)
        # Splitting the text into chunks
        text_chunks = chunk_text(document_text)
        # Generating embeddings for the text chunks
        embeddings = embedding_model.encode(text_chunks).tolist()

        # Uploading the embeddings and corresponding text chunks to Pinecone
        upload_embeddings_to_pinecone(embeddings, text_chunks)
        st.success("✅ PDF processed and embeddings uploaded!")

        # Creating a form to capture the query input and submit the form
        with st.form(key='query_form', clear_on_submit=True):
            query = st.text_input("💬 Ask any question about the document", key="query_input")
            submit_button = st.form_submit_button(label="Submit")

            # If the submit button is clicked and there's a query
            if submit_button:
                if query:
                    # Show spinner while processing the query
                    with st.spinner("⏳ Processing your request..."):
                        # Retrieve relevant chunks
                        retrieved_texts = retrieve_relevant_chunks(query)

                        # Generate answer based on retrieved texts and user query
                        answer = generate_answer(retrieved_texts, query)

                    # Add the query and generated answer to the chat history
                    st.session_state["chat_history"].append({"query": query, "answer": answer, "retrieved_text": retrieved_texts})

    # Display the chat history, showing the most recent interactions first
    if st.session_state["chat_history"]:
        for chat in reversed(st.session_state["chat_history"]):   # Reversing the order to show the latest query first
            st.write(f"**You:** {chat['query']}")
            st.write(f"**Bot:** {chat['answer']}")
            st.write("---")

# Starting the Streamlit app
start_streamlit()
