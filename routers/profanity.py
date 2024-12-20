from fastapi import APIRouter, Query, HTTPException
from better_profanity import profanity

# Create a router for the profanity API
router = APIRouter()

@router.get("/profanity/check/")
def check_profanity(text: str = Query(..., description="Text to be checked")):
    """
    Check if the text contains profanity and return details.
    """
    try:
        # Load default dictionary
        profanity.load_censor_words()

        # Detect profanity
        contains_profanity = profanity.contains_profanity(text)
        censored_text = profanity.censor(text)

        # Extract offensive words
        words = text.split()
        offensive_words = [
            word for word in words if profanity.contains_profanity(word)
        ]

        # Calculate percentage of offensive words
        offensive_word_count = len(offensive_words)
        total_word_count = len(words)
        offensive_percentage = (
            (offensive_word_count / total_word_count) * 100 if total_word_count > 0 else 0
        )

        # Return response
        return {
            "original_text": text,
            "contains_profanity": contains_profanity,
            "censored_text": censored_text,
            "offensive_words": offensive_words,
            "offensive_word_count": offensive_word_count,
            "total_word_count": total_word_count,
            "offensive_percentage": offensive_percentage,
        }
    except Exception as e:
        # Handle errors gracefully
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while processing the text: {str(e)}"
        )
