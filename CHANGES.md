# Changes in v2

## SoundCloud
- Replaced the old direct-file `<audio>` player with responsive SoundCloud iframe widgets.
- Accepts normal public SoundCloud track URLs from the Excel `recordings` sheet.
- Added SoundCloud Widget API lyric progress highlighting and click-to-seek.
- Supports more than one SoundCloud recording on the same hymn page without custom speed buttons.

## Spreadsheet behaviour
- Language defaults now render on the server before JavaScript runs.
- Changing `default_on` in Excel invalidates old browser language preferences automatically.
- Added a Reset languages button.
- Added language `sort` support.
- Added validation warnings for missing/duplicate links between workbook sheets.
- Added a professionally formatted Instructions sheet.

## Accounts and roles
- Added secure session login.
- Added Student, Teacher, and Administrator roles.
- The signed-in user's display name and role appear in the top-right corner.
- Added password change and administrator password reset tools.

## Student tools
- Private audio practice uploads.
- Submission history, status, and teacher feedback.
- Authenticated audio delivery instead of public static upload URLs.

## Teacher tools
- Student submission review with feedback and status.
- Attendance for assigned classes.
- Developer comment submission.

## Administrator tools
- Create/deactivate users and reset passwords.
- Create classes, assign teachers, and enrol students.
- Access all attendance and student submission pages.
- Review and update developer comment statuses.

## Errors fixed
- Removed the duplicate `langStoreKey` JavaScript declaration that stopped all page JavaScript.
- Removed duplicated player/language controls and duplicated element IDs from the hymn template.
- Removed obsolete Nextcloud/native-audio code.
- Replaced native audio progress logic with SoundCloud widget events.
- Fixed spreadsheet language order/default handling.
- Added safer composite workbook linking and content warnings.
- Removed the content named volume that prevented rebuilt Excel content from appearing.
- Added friendly content, 403, and 404 error pages.
- Added upload type/size checks, password hashing, CSRF protection, secure sessions, and permission checks.


## Administrator recovery update

- Added `/setup-admin`, a browser-based administrator creation/reset page.
- Protected the page with the `ADMIN_SETUP_KEY` Portainer environment variable.
- Successful recovery automatically signs the administrator in and opens user management.
- No Portainer console or pasted shell commands are required.
