# Stock_ID Migration Implementation Summary

## Problem Identified
The `first_limit_topics` table was using `first_limit_id` as the primary reference. When historical limit-up data is refreshed via `/api/limit-stats/refresh`, the `INSERT OR REPLACE` causes record ID changes due to `UNIQUE(stock_id, limit_date)` constraint, resulting in orphaned references in `first_limit_topics` table.

## Solution Implemented
Migrated from `first_limit_id`-based references to `stock_id + association_date`-based references. This makes the system resilient to data refreshes since `stock_id` is stable and never changes.

## Changes Made

### 1. Database Migration (Completed ✓)
- Added `stock_id` column to `first_limit_topics` table
- Populated `stock_id` values from `first_limits` table
- Removed 12 orphaned records that couldn't be restored
- Successfully migrated 90 records with valid stock_id values

**Scripts Created:**
- `migrate_to_stock_id.py` - Main migration script to fix orphaned records
- `add_stock_id_column.py` - Adds stock_id column and populates it
- `fix_stock_id_population.py` - Repairs stock_id values using correct column name

**Final State:**
- Total records in first_limit_topics: 90
- Records with NULL stock_id: 0
- Orphaned first_limit_id references: 0
- Invalid stock_id references: 0

### 2. Backend Changes (db_operations.py) ✓
**Modified Functions:**

1. `insert_into_first_limit_topic()`
   - Changed duplicate check from `(first_limit_id, topic_id, association_date)` to `(stock_id, topic_id, association_date)`
   - Returns stock_id from first_limits table and uses it for insert
   - Now includes stock_id in the INSERT statement

2. `remove_first_limit_topic()`
   - Changed parameter from `first_limit_id` to `stock_id`
   - Changed deletion logic to delete by `(stock_id, topic_id, association_date)`

### 3. Backend Changes (main.py) ✓
**Modified API Endpoint:**

1. `/api/remove-first-limit-topic` (POST)
   - Changed parameter from `first_limit_id` to `stock_id`
   - Removed the need to query `get_stock_id_by_first_limit_id()`
   - Now directly uses `stock_id` from request body
   - Simplified logic - no need to look up stock_id anymore

### 4. Frontend Changes (dashboard.html) ✓
**Modified Functions:**

1. `dragStart()`
   - Added `stock_id` parameter
   - Sets `stock_id` in dataTransfer

2. Stock Card Rendering (line 5475)
   - Updated `ondragstart` call to pass `stock.stock_id`
   - Added `data-stock-id` attribute

3. `dropToTopic()`
   - Retrieves stock_id from dataTransfer
   - Includes stock_id in stockData object

4. `saveStockToTopic()`
   - Sends `stock_id` instead of `first_limit_id` to backend API
   - Falls back to `id` if stock_id not available (backward compatibility)

5. `removeFromTopic()`
   - Now checks for `stock.stock_id` instead of `stock.id`
   - Sends `stock_id` instead of `first_limit_id` to remove API

## Data Flow After Migration

### Adding a Stock to Topic:
1. Frontend: User drags stock card → `dropToTopic()` → `saveStockToTopic()`
2. API Endpoint: `/api/add-first-limit-to-topic`
   - Receives: `stock_id`, `topic_id`, `date`, association_date`
   - Backend: `db.insert_into_first_limit_topic(stock_id, ...)`
3. Database: Check duplicate by `(stock_id, topic_id, association_date)`
   - If unique: INSERT record with `stock_id` and `first_limit_id`
   - If duplicate: Log message, skip insert

### Removing a Stock from Topic:
1. Frontend: Click remove button → `removeFromTopic()` → API call
2. API Endpoint: `/api/remove-first-limit-topic`
   - Receives: `stock_id`, `topic_id`, `association_date`, `remove_relation`
   - Backend: `db.remove_first_limit_topic(stock_id, topic_id, association_date)`
3. Database: DELETE by `(stock_id, topic_id, association_date)`

### Loading Topic Data (/api/dashboard):
1. API calls `db.get_topic_first_limits_by_association_date(topic_id, date)`
2. Query joins `first_limit_topics` by `association_date` and returns data
3. Frontend receives both `id` (first_limit_id) and `stock_id` in stock objects
4. Frontend uses `stock_id + association_date` for operations

## Benefits

1. **Data Integrity**: Associations survive data refreshes since stock_id is stable
2. **Logical Consistency**: Stocks (business entities) rather than database record IDs
3. **Simpler Logic**: No need to query stock_id from first_limit_id anymore
4. **Backward Compatible**: first_limit_id still exists in table for reference

## Testing Needed

1. **Add stock to topic** - Verify duplicate checking works correctly
2. **Remove stock from topic** - Verify deletion by stock_id + association_date works
3. **Data refresh** - Refresh historical data and verify topic associations remain intact
4. **Historical data view** - Check topic rotation analysis shows correct stocks
5. **Corner cases**:
   - Same stock hits limit-up on multiple dates
   - Viewing historical dates with topic cards

## Important Notes

1. The `first_limit_id` column is still present in `first_limit_topics` table for backward compatibility
2. Both `id` (first_limit_id) and `stock_id` are sent to frontend from `/api/dashboard`
3. Frontend sends `stock_id` to backend for add/remove operations
4. The UNIQUE constraint should eventually be updated to `(stock_id, topic_id, association_date)` if needed

## Files Modified

1. **Database**:
   - `migrate_to_stock_id.py` (new)
   - `add_stock_id_column.py` (new)
   - `fix_stock_id_population.py` (new)

2. **Backend (Python)**:
   - `src/db_operations.py` - 2 functions modified
   - `src/main.py` - 1 API endpoint modified

3. **Frontend (JavaScript)**:
   - `templates/dashboard.html` - 5 functions modified

## Verification Commands

```bash
# Check data integrity
python -c "import sqlite3; conn = sqlite3.connect('data/fupan.db'); c = conn.cursor(); c.execute('SELECT COUNT(*) FROM first_limit_topics WHERE stock_id IS NULL'); print(f'NULL stock_id: {c.fetchone()[0]}'); c.execute('SELECT COUNT(DISTINCT first_limit_id) FROM first_limit_topics WHERE first_limit_id NOT IN (SELECT id FROM first_limits)'); print(f'Orphaned first_limit_ids: {c.fetchone()[0]}'); conn.close()"

# Start server
python start.py
```

## Next Steps

1. Test all add/remove operations manually
2. Test data refresh `/api/limit-stats/refresh` and verify associations persist
3. Test viewing historical dates with topic cards
4. Consider updating UNIQUE constraint to `(stock_id, topic_id, association_date)` for extra safety
5. Update any remaining documentation that references first_limit_id
