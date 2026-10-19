"""Scoped PostgreSQL SELECT adapter, deliberately independent of app.main."""
import json
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.models.agent import AgentConversation, AgentRun, AgentMessage, AgentToolCall
from session_recovery import ObservationRejected


def serial(row):
    return json.loads(json.dumps({c.name: getattr(row, c.name) for c in row.__table__.columns}, default=str))


class SqlObserver:
    def __init__(self, database_url):
        self.database_url = database_url
        self.calls = 0

    async def __call__(self, scope, run_ids):
        # Caller validates the explicit registry and prefix. Validate syntax here
        # too so using this adapter directly cannot interpolate an identifier.
        import re
        if not isinstance(scope.schema, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,19}_[0-9a-f]{32}', scope.schema):
            raise ObservationRejected('invalid_schema')
        self.calls += 1
        engine = create_async_engine(self.database_url, poolclass=NullPool,
                                    connect_args={'server_settings': {'search_path': scope.schema,
                                                'timezone': 'UTC', 'default_transaction_read_only': 'on',
                                                'statement_timeout': '10000'}})
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                readonly = (await db.execute(text('SHOW transaction_read_only'))).scalar_one() == 'on'
                if not readonly:
                    raise ObservationRejected('read_only_not_enforced')
                exists = (await db.execute(text('SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)'), {'schema': scope.schema})).scalar_one()
                if not exists:
                    raise ObservationRejected('schema_missing_in_sql')
                conv = await db.scalar(select(AgentConversation.id).where(AgentConversation.id == scope.conversation_id, AgentConversation.user_id == scope.user_id))
                if conv is None:
                    raise ObservationRejected('conversation_scope_mismatch')
                rows = list((await db.scalars(select(AgentRun).where(AgentRun.conversation_id == scope.conversation_id).order_by(AgentRun.started_at, AgentRun.id))).all())
                if any(r.user_id != scope.user_id for r in rows):
                    raise ObservationRejected('run_scope_mismatch')
                ids = {r.id for r in rows}
                if not set(run_ids) <= ids:
                    raise ObservationRejected('referenced_run_missing_or_foreign')
                messages = list((await db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == scope.conversation_id, AgentMessage.run_id.in_(ids)).order_by(AgentMessage.created_at, AgentMessage.id))).all())
                audits = list((await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id.in_(ids)).order_by(AgentToolCall.created_at, AgentToolCall.id))).all())
                return dict(scope_validated=True, read_only=True, isolation='repeatable read',
                            runs=[serial(r) for r in rows], messages=[serial(r) for r in messages],
                            tool_audits=[serial(r) for r in audits])
        finally:
            await engine.dispose()
