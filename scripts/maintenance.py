"""Run daily as an OS scheduled job. Deletes expired visit logs, never audit records."""
from datetime import datetime,timedelta,timezone
from sqlalchemy import delete
from app.models import Session,VerificationLog,StaffSession,LoginAttempt,AuditLog,Setting,now
from app.config import limit
from app.services import dumps

with Session() as db:
    row=db.get(Setting,'visit_log_retention_days')
    days=int(row.value) if row else limit('VISIT_LOG_RETENTION_DAYS',360)
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    result=db.execute(delete(VerificationLog).where(VerificationLog.visited_at<cutoff))
    db.execute(delete(StaffSession).where(StaffSession.expires_at<now()))
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at<(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()))
    db.add(AuditLog(actor_id=None,action='按保留期限清理访问日志',details_json=dumps({'retention_days':days,'deleted':result.rowcount}),source_ip='scheduled-job'))
    db.commit()
print('Retention maintenance completed; audit records preserved.')
