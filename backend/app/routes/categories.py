import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..database import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/categories", tags=["categories"])

class CategoryCreate(BaseModel):
    name: str

class CategoryUpdate(BaseModel):
    name: str

@router.get("")
async def list_categories():
    return await db.get_categories()

@router.post("")
async def create_category(cat: CategoryCreate):
    try:
        id = await db.create_category(cat.name)
        return {"id": id, "name": cat.name}
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create category")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{id}")
async def update_category(id: int, cat: CategoryUpdate):
    try:
        await db.update_category(id, cat.name)
        return {"success": True}
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update category {id}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/{id}")
async def delete_category(id: int):
    try:
        await db.delete_category(id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to delete category {id}")
        raise HTTPException(status_code=500, detail="Internal server error")
