::	Phoronix Test Suite
::	URLs: http://www.phoronix.com, http://www.phoronix-test-suite.com/
::	Copyright (C) 2008 - 2019, Phoronix Media
::	Copyright (C) 2008 - 2019, Michael Larabel
::	phoronix-test-suite: The Phoronix Test Suite is an extensible open-source testing / benchmarking platform
::
::	This program is free software; you can redistribute it and/or modify
::	it under the terms of the GNU General Public License as published by
::	the Free Software Foundation; either version 3 of the License, or
::	(at your option) any later version.
::
::	This program is distributed in the hope that it will be useful,
::	but WITHOUT ANY WARRANTY; without even the implied warranty of
::	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
::	GNU General Public License for more details.
::
::	You should have received a copy of the GNU General Public License
::	along with this program. If not, see <http://www.gnu.org/licenses/>.
::

:: Full path to root directory of the actual Phoronix Test Suite code
@echo off
set PTS_DIR=%~dp0
set PTS_MODE=CLIENT
set PTS_LAUNCHER=%0

:: See if php was setup via Cygwin64, the benefit there is allowing access to PCNTL extensions, etc
:: If exist C:\cygwin64\bin\php.exe (
:: set PHP_BIN=C:\cygwin64\bin\php.exe
:: )

:: TODO: Other work to bring this up to sync with the *NIX phoronix-test-suite launcher
If defined PHP_BIN goto SkipBinSearch
  
:: Download PHP for Windows and then extract it
If not exist C:\PHP\php.exe (
echo Attempting to download and setup Windows PHP release.
If not exist php.zip (
powershell -command "& { iwr http://phoronix-test-suite.com/benchmark-files/php-7.2.3-Win32-VC15-x64.zip -OutFile php.zip }"
)
powershell -command "& { Expand-Archive php.zip -DestinationPath C:\PHP }"
If not exist VC_redist.x64.exe (
echo Attempting to download and run Visual C++ Redistributable for Visual Studio 2017 support.
powershell -command "& { iwr https://go.microsoft.com/fwlink/?LinkId=746572 -OutFile VC_redist.x64.exe }"
VC_redist.x64.exe
)
  )
:: Use the newly downloaded PHP location
set PHP_BIN=C:\PHP\php.exe

:SkipBinSearch

%PHP_BIN% "%PTS_DIR%\pts-core\phoronix-test-suite.php" %*